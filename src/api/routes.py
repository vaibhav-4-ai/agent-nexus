"""
FastAPI routes — all API endpoints for agent-nexus.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.api.schemas import (
    HealthResponse, MCPServerListResponse, MCPToolInfo, MetricsResponse,
    TaskCreateRequest, TaskCreateResponse, TaskFeedbackRequest, TaskStatusResponse,
)
from src.infra.logging import get_logger

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

    # Initialize task result with an empty trace
    _task_results[task_id] = {
        "task_id": task_id, 
        "status": "queued", 
        "goal": request.goal,
        "execution_trace": []
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
                result = await engine.execute_task(task_id, request.goal, attachments)
                # Ensure we don't overwrite if engine returned a different dict instance
                _task_results[task_id].update(result)
            finally:
                _active_tasks -= 1

    _task_futures[task_id] = asyncio.create_task(_run())

    return TaskCreateResponse(task_id=task_id, status="queued")


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str) -> TaskStatusResponse:
    """Get the current status and result of a task."""
    result = _task_results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return TaskStatusResponse(**result)


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
    logger.info("task_feedback", task_id=task_id, feedback=request.feedback)
    return {"status": "feedback_received", "task_id": task_id}


# ---------------------------------------------------------------------------
# WebSocket streaming
# ---------------------------------------------------------------------------
@router.websocket("/tasks/{task_id}/stream")
async def stream_task(websocket: WebSocket, task_id: str) -> None:
    """Stream real-time task execution updates via WebSocket."""
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

    return HealthResponse(components=components)


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics_endpoint() -> MetricsResponse:
    """Get agent metrics for the dashboard."""
    from src.infra.metrics import get_metrics
    metrics = await get_metrics()
    return MetricsResponse(
        summary=metrics.get_summary(),
        all_metrics=metrics.get_all_metrics(),
    )
