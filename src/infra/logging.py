"""
Structured logging configuration for agent-nexus.

Uses structlog with JSON output for production-grade observability.
Every log line includes: timestamp, level, component, action, and optional
task_id + duration_ms for request-scoped tracing.
"""

from __future__ import annotations

import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Context variables for per-request tracing
# ---------------------------------------------------------------------------
_task_id_ctx: ContextVar[str | None] = ContextVar("task_id", default=None)
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_task_context(task_id: str) -> None:
    """Bind a task_id to the current async context for all subsequent log lines."""
    _task_id_ctx.set(task_id)


def bind_request_context(request_id: str) -> None:
    """Bind a request_id to the current async context."""
    _request_id_ctx.set(request_id)


def clear_context() -> None:
    """Clear all context variables."""
    _task_id_ctx.set(None)
    _request_id_ctx.set(None)


# ---------------------------------------------------------------------------
# Custom structlog processors
# ---------------------------------------------------------------------------
def _add_context_vars(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Inject context variables (task_id, request_id) into every log line."""
    task_id = _task_id_ctx.get()
    request_id = _request_id_ctx.get()
    if task_id:
        event_dict["task_id"] = task_id
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def _add_component(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add component name from the logger name."""
    if "component" not in event_dict:
        event_dict["component"] = event_dict.get("_record", {}).get("name", "agent-nexus")
    return event_dict


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """
    Configure structlog for the entire application.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        json_format: If True, output JSON logs. If False, output colored console logs.
    """
    # Shared processors for both structlog and stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_context_vars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Suppress noisy third-party loggers
    for noisy_logger in ("httpx", "httpcore", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger bound to a specific component.

    Args:
        component: Name of the component (e.g., 'orchestrator', 'mcp.client').

    Returns:
        A bound structlog logger.

    Usage:
        logger = get_logger("orchestrator.engine")
        logger.info("task_started", task_id="abc-123", goal="Find bugs")
    """
    return structlog.get_logger(component).bind(component=component)


class Timer:
    """
    Context manager for timing operations and logging duration.

    Usage:
        logger = get_logger("llm")
        with Timer(logger, "llm_call", model="gpt-4"):
            result = await llm.call(...)
    """

    def __init__(
        self,
        logger: structlog.stdlib.BoundLogger,
        action: str,
        **extra: Any,
    ) -> None:
        self.logger = logger
        self.action = action
        self.extra = extra
        self._start: float = 0.0
        self.duration_ms: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.duration_ms = (time.perf_counter() - self._start) * 1000
        if exc_type is not None:
            self.logger.error(
                f"{self.action}_failed",
                duration_ms=round(self.duration_ms, 2),
                error=str(exc_val),
                error_type=exc_type.__name__,
                **self.extra,
            )
        else:
            self.logger.info(
                f"{self.action}_completed",
                duration_ms=round(self.duration_ms, 2),
                **self.extra,
            )
