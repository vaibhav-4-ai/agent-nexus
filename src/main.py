"""
FastAPI Application Entry Point.

Wires together all components: database, Redis, vector store, MCP servers,
orchestrator engine, and API routes.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_settings
from src.infra.logging import get_logger, setup_logging

logger = get_logger("main")

# Module-level singletons (initialized in lifespan)
_orchestrator_engine: Any = None
_mcp_registry: Any = None
_llm_key_status: str = "unknown"


def get_orchestrator_engine() -> Any:
    return _orchestrator_engine


def get_mcp_registry() -> Any:
    return _mcp_registry


def get_llm_key_status() -> str:
    """Returns 'configured', 'ollama_only', or 'no_key_configured'."""
    return _llm_key_status


def _detect_llm_key_status() -> str:
    """Detect what kind of LLM auth is available."""
    settings = get_settings()
    if settings.llm.model.startswith("ollama/"):
        return "ollama_only"
    keys = [
        settings.llm.groq_api_key.get_secret_value(),
        settings.llm.openai_api_key.get_secret_value(),
        settings.llm.anthropic_api_key.get_secret_value(),
        settings.llm.gemini_api_key.get_secret_value(),
    ]
    if any(keys):
        return "configured"
    # Fall back to OS env (in case keys were exported instead of in .env)
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(k):
            return "configured"
    return "no_key_configured"


_prune_task: asyncio.Task[Any] | None = None
_task_cleanup_task: asyncio.Task[Any] | None = None
_quota_manager_task: asyncio.Task[Any] | None = None


async def _background_pruner() -> None:
    """Prunes DB data older than 1 day. Runs every hour."""
    from src.infra.db import prune_old_data
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            await prune_old_data(days_to_keep=1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("background_pruner_error", error=str(e))
            await asyncio.sleep(600)


async def _background_task_cleanup() -> None:
    """Evicts terminal in-memory task entries older than the configured cutoff."""
    from src.api.routes import _task_results, _task_futures
    settings = get_settings()
    interval = min(3600, max(60, settings.monitoring.task_cleanup_cutoff_s))
    while True:
        try:
            await asyncio.sleep(interval)
            cutoff_s = settings.monitoring.task_cleanup_cutoff_s
            cutoff_time = time.time() - cutoff_s
            stale = [
                tid for tid, r in list(_task_results.items())
                if r.get("_created_at", time.time()) < cutoff_time
                and r.get("status") in ("completed", "failed", "cancelled")
            ]
            for tid in stale:
                _task_results.pop(tid, None)
                _task_futures.pop(tid, None)
            if stale:
                logger.info("task_results_cleaned", removed=len(stale),
                            remaining=len(_task_results))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("task_cleanup_error", error=str(e))
            await asyncio.sleep(600)


async def _background_quota_manager() -> None:
    """Auto-evicts Qdrant vectors / knowledge_graph rows when approaching free-tier limits."""
    from src.infra.alerts import QuotaManager
    settings = get_settings()
    manager = QuotaManager()
    while True:
        try:
            await asyncio.sleep(settings.monitoring.quota_check_interval_s)
            await manager.check_and_remediate()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("quota_manager_error", error=str(e))
            await asyncio.sleep(600)


async def _recover_stale_tasks() -> None:
    """On startup: mark non-terminal tasks in DB as FAILED (server restarted mid-execution)."""
    try:
        from src.infra.db import _get_session_factory, TaskRepository, TaskStatus
        factory = _get_session_factory()
        async with factory() as session:
            repo = TaskRepository(session)
            non_terminal_statuses = [
                TaskStatus.PENDING, TaskStatus.PARSING, TaskStatus.PLANNING,
                TaskStatus.EXECUTING, TaskStatus.VERIFYING, TaskStatus.RECOVERING,
            ]
            recovered = 0
            for st in non_terminal_statuses:
                tasks = await repo.list_tasks(limit=1000, status=st)
                for t in tasks:
                    await repo.update_status(
                        t.id, TaskStatus.FAILED,
                        error="Server restarted before task completed",
                    )
                    recovered += 1
            await session.commit()
            if recovered:
                logger.info("stale_tasks_recovered", count=recovered)
    except Exception as e:
        logger.warning("stale_task_recovery_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown hooks."""
    global _orchestrator_engine, _mcp_registry, _prune_task
    global _task_cleanup_task, _quota_manager_task, _llm_key_status

    settings = get_settings()
    setup_logging(log_level=settings.log_level, json_format=settings.environment.value != "development")
    logger.info("starting_agent_nexus", environment=settings.environment.value)

    # Detect LLM key status BEFORE anything else, so health endpoint can report it
    _llm_key_status = _detect_llm_key_status()
    if _llm_key_status == "no_key_configured":
        logger.warning(
            "no_llm_key_configured",
            hint="Set GROQ_API_KEY (free at console.groq.com), or GEMINI_API_KEY / "
                 "OPENAI_API_KEY / ANTHROPIC_API_KEY in .env. Or run with "
                 "`docker compose --profile local up` for Ollama (no key needed). "
                 "Tasks will fail until an LLM is configured.",
        )

    # --- Startup ---
    # 1. Initialize database
    try:
        from src.infra.db import init_db
        await init_db()
        logger.info("database_ready")
    except Exception as e:
        logger.error("database_init_failed", error=str(e))

    # 1b. Recover any stale in-flight tasks from a previous run
    await _recover_stale_tasks()

    # 2. Initialize Redis
    try:
        from src.infra.redis_client import get_redis
        await get_redis()
        logger.info("redis_ready")
    except Exception as e:
        logger.warning("redis_init_failed", error=str(e))

    # 3. Initialize Vector Store
    try:
        from src.infra.vector_store import get_vector_store
        await get_vector_store()
        logger.info("vector_store_ready")
    except Exception as e:
        logger.warning("vector_store_init_failed", error=str(e))

    # 4. Initialize MCP servers
    from src.mcp.client import MCPClient
    from src.mcp.registry import ServerRegistry

    _mcp_registry = ServerRegistry()
    mcp_client = MCPClient(_mcp_registry)
    await mcp_client.initialize_all_servers()
    logger.info("mcp_servers_ready")

    # 5. Initialize Event Bus
    from src.infra.event_bus import get_event_bus
    event_bus = get_event_bus()
    await event_bus.start()

    # 6. Initialize Metrics
    from src.infra.metrics import get_metrics
    await get_metrics()

    # 7. Create Orchestrator Engine
    from src.orchestrator.engine import OrchestratorEngine
    _orchestrator_engine = OrchestratorEngine(mcp_client, event_bus)

    # 8. Start background tasks (DB pruner + in-memory cleanup + quota manager)
    _prune_task = asyncio.create_task(_background_pruner())
    _task_cleanup_task = asyncio.create_task(_background_task_cleanup())
    _quota_manager_task = asyncio.create_task(_background_quota_manager())

    logger.info("agent_nexus_ready", port=settings.api.port,
                llm_status=_llm_key_status)

    yield  # Application runs here

    # --- Shutdown ---
    logger.info("shutting_down_agent_nexus")

    for task in (_prune_task, _task_cleanup_task, _quota_manager_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await event_bus.stop()
    await _mcp_registry.shutdown_all()

    try:
        from src.infra.redis_client import close_redis
        await close_redis()
    except Exception:
        pass

    try:
        from src.infra.vector_store import close_vector_store
        await close_vector_store()
    except Exception:
        pass

    try:
        from src.infra.db import close_db
        await close_db()
    except Exception:
        pass

    logger.info("agent_nexus_shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="agent-nexus",
        description="Multimodal AI Agent that sees, hears, reads code, queries databases, "
                    "and reasons across all modalities — with self-healing and grounded verification.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Apply middleware
    from src.api.middleware import setup_middleware
    setup_middleware(app)

    # Mount API routes
    from src.api.routes import router
    app.include_router(router)

    # NOTE (S6): the previous `/workspace` StaticFiles mount served files
    # generated by the agent with no auth. Removed — agent responses already
    # include file contents inside the execution trace, so no separate file
    # download route is needed. The workspace directory still exists on disk
    # for the MCP filesystem server, just not exposed via HTTP.
    workspace_dir = Path(settings.mcp.workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Frontend route
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def root() -> str:
            return "<html><body><h1>agent-nexus API is running, but frontend was not found.</h1></body></html>"

    return app


# Create the app instance (used by uvicorn)
app = create_app()


def main() -> None:
    """Run the application."""
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=settings.api.debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
