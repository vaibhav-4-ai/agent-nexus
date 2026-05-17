"""
FastAPI routes — all API endpoints for agent-nexus.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

# `require_api_key` is imported (but not currently applied to any endpoint)
# so re-enabling auth on a specific endpoint is a one-line edit:
#     @router.get("/foo", dependencies=[Depends(require_api_key)])
from src.api.middleware import require_api_key  # noqa: F401 — kept for easy re-enable
from src.api.schemas import (
    HealthResponse, MCPServerListResponse, MCPToolInfo, MetricsResponse,
    TaskCreateRequest, TaskCreateResponse, TaskFeedbackRequest, TaskStatusResponse,
)
from src.infra.logging import get_logger, redact_secrets

logger = get_logger("api.routes")

router = APIRouter(prefix="/api/v1")

# In-memory task results store (persisted to DB in production via engine)
_task_results: dict[str, dict[str, Any]] = {}
_task_futures: dict[str, asyncio.Task[Any]] = {}


def _get_engine():
    """Lazy import to avoid circular dependencies."""
    from src.main import get_orchestrator_engine
    return get_orchestrator_engine()


def _get_mcp_registry():
    from src.main import get_mcp_registry
    return get_mcp_registry()


# ---------------------------------------------------------------------------
# Task endpoints
# Global concurrency limit to protect free-tier API rate limits
_MAX_CONCURRENT_TASKS = 3
_task_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TASKS)
_active_tasks = 0

@router.post("/tasks", response_model=TaskCreateResponse)
async def create_task(request: TaskCreateRequest) -> TaskCreateResponse:
    """Create a new task for the agent to execute."""
    global _active_tasks
    task_id = str(uuid.uuid4())
    logger.info("task_create_request", task_id=task_id, goal=request.goal[:100])

    engine = _get_engine()
    attachments = [a.model_dump() for a in request.attachments] if request.attachments else None

    # Per-request BYOK override (if any). Convert SecretStr to plain string here
    # so it doesn't show in logs from str(request) — but never log this dict.
    byok_override: dict[str, Any] | None = None
    if request.byok is not None:
        byok_override = {
            "provider": request.byok.provider,
            "model": request.byok.model,
            "api_key": request.byok.api_key.get_secret_value(),
        }
        # Log only that BYOK was used + which provider — never the key.
        logger.info("task_byok_used", task_id=task_id,
                    provider=request.byok.provider, model=request.byok.model)

    # Initialize task result with an empty trace
    _task_results[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "goal": request.goal,
        "execution_trace": [],
        "_created_at": time.time(),  # used by background cleanup
    }

    # Run task in background
    async def _run() -> None:
        global _active_tasks
        # If the system is busy, notify the UI via the trace
        if _active_tasks >= _MAX_CONCURRENT_TASKS:
            _task_results[task_id]["execution_trace"].append({
                "step_number": 0,
                "description": "Task successfully queued. Waiting for optimal compute allocation...",
                "status": "pending",
                "tool": "Task Scheduler"
            })

        async with _task_semaphore:
            _active_tasks += 1
            try:
                _task_results[task_id]["status"] = "running"
                result = await engine.execute_task(
                    task_id, request.goal, attachments, byok=byok_override,
                )
                # Ensure we don't overwrite if engine returned a different dict instance
                _task_results[task_id].update(result)
            finally:
                _active_tasks -= 1

    _task_futures[task_id] = asyncio.create_task(_run())

    return TaskCreateResponse(task_id=task_id, status="queued")


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str) -> TaskStatusResponse:
    """Get the current status and result of a task.

    Tries the in-memory store first (fast path for live tasks), then falls back
    to the database (so tasks survive server restarts).
    """
    result = _task_results.get(task_id)
    if result is None:
        result = await _load_task_from_db(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return TaskStatusResponse(**result)


async def _load_task_from_db(task_id: str) -> dict[str, Any] | None:
    """Best-effort load of a task from Postgres. Returns dict or None if not found / DB unavailable."""
    try:
        from src.infra.db import _get_session_factory, TaskRepository
        factory = _get_session_factory()
        async with factory() as session:
            repo = TaskRepository(session)
            task = await repo.get(task_id)
            if task is None:
                return None
            return {
                "task_id": task.id,
                "status": task.status,
                "goal": task.goal,
                "execution_trace": task.execution_trace or [],
                "result": task.result,
                "error": task.error,
                "total_steps": task.total_steps,
                "completed_steps": task.completed_steps,
                "duration_ms": task.total_duration_ms,
                "created_at": task.created_at,
            }
    except Exception as e:
        logger.warning("task_db_lookup_failed", task_id=task_id, error=str(e))
        return None


@router.get("/tasks/{task_id}/evidence")
async def get_task_evidence(task_id: str) -> dict[str, Any]:
    """Get the full evidence chain for a task."""
    result = _task_results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {
        "task_id": task_id,
        "evidence_chain": result.get("execution_trace", []),
    }


@router.post("/tasks/{task_id}/feedback")
async def submit_feedback(task_id: str, request: TaskFeedbackRequest) -> dict[str, str]:
    """Submit human-in-the-loop feedback for a running task."""
    # L3-A: truncate + redact before logging. User-submitted prose could in
    # principle contain credentials they pasted. Don't log them in clear.
    preview = redact_secrets(request.feedback or "")[:200]
    logger.info("task_feedback", task_id=task_id, feedback_preview=preview)
    return {"status": "feedback_received", "task_id": task_id}


# ---------------------------------------------------------------------------
# WebSocket streaming
# ---------------------------------------------------------------------------
@router.websocket("/tasks/{task_id}/stream")
async def stream_task(websocket: WebSocket, task_id: str) -> None:
    """Stream real-time task execution updates via WebSocket.

    Public (v10): the task_id (UUIDv4, 128 bits of entropy) acts as a bearer.
    Anyone who knows the task_id can watch its stream; guessing it is
    infeasible. The `require_api_key` dependency is intentionally NOT applied
    here for the public-demo deployment.
    """
    await websocket.accept()
    try:
        # Poll for updates
        last_trace_len = 0
        while True:
            result = _task_results.get(task_id, {})
            trace = result.get("execution_trace", [])

            # Send new steps
            if len(trace) > last_trace_len:
                for step in trace[last_trace_len:]:
                    await websocket.send_json({
                        "type": "step_update",
                        "data": step,
                        "task_id": task_id,
                    })
                last_trace_len = len(trace)

            # Check if task is done
            status = result.get("status", "")
            if status in ("completed", "failed"):
                await websocket.send_json({
                    "type": "task_completed",
                    "data": result,
                    "task_id": task_id,
                })
                break

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", task_id=task_id)


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------
@router.get("/mcp/servers", response_model=MCPServerListResponse)
async def list_mcp_servers() -> MCPServerListResponse:
    """List all available MCP servers and their tools."""
    registry = _get_mcp_registry()
    servers = []
    tools = []

    for name, server in registry.get_all_servers().items():
        server_tools = server.list_tools()
        servers.append({
            "name": name,
            "tools_count": len(server_tools),
            "tools": [t.name for t in server_tools],
        })
        for t in server_tools:
            tools.append(MCPToolInfo(
                server=name,
                name=t.name,
                description=t.description,
                parameters=t.to_schema()["parameters"],
            ))

    return MCPServerListResponse(servers=servers, tools=tools, total_tools=len(tools))


# ---------------------------------------------------------------------------
# Health & Metrics
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check for all components."""
    components: dict[str, str] = {"api": "healthy", "orchestrator": "healthy"}

    # Check database
    try:
        from src.infra.db import _get_engine
        engine = _get_engine()
        if engine:
            components["database"] = "healthy"
    except Exception:
        components["database"] = "unavailable"

    # Check Redis
    try:
        from src.infra.redis_client import get_redis
        redis = await get_redis()
        components["redis"] = "fallback" if redis._using_fallback else "healthy"
    except Exception:
        components["redis"] = "unavailable"

    # LLM key status (configured / ollama_only / no_key_configured)
    try:
        from src.main import get_llm_key_status
        llm_status = get_llm_key_status()
        components["llm"] = llm_status
    except Exception:
        components["llm"] = "unknown"

    # Vector store (reported as: qdrant_cloud / local / unavailable)
    try:
        from src.config import get_settings
        from src.infra.vector_store import get_vector_store
        vs = await get_vector_store()
        if vs is None:
            components["vector_store"] = "unavailable"
        else:
            settings = get_settings()
            has_cloud_key = bool(settings.vector_db.api_key.get_secret_value())
            components["vector_store"] = "qdrant_cloud" if has_cloud_key else "local"
    except Exception:
        components["vector_store"] = "unavailable"

    overall_status = "healthy"
    if components.get("llm") == "no_key_configured":
        overall_status = "degraded"

    return HealthResponse(status=overall_status, components=components)


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics_endpoint() -> MetricsResponse:
    """Get agent metrics for the dashboard."""
    from src.infra.metrics import get_metrics
    metrics = await get_metrics()
    return MetricsResponse(
        summary=metrics.get_summary(),
        all_metrics=metrics.get_all_metrics(),
    )
