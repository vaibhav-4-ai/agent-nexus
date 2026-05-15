"""
API Middleware — CORS, rate limiting, request ID, logging, error handling.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import get_settings
from src.infra.logging import bind_request_context, get_logger

logger = get_logger("api.middleware")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Adds a unique request ID to every request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        request_id = str(uuid.uuid4())[:8]
        bind_request_context(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with duration."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # Skip health check spam
        if request.url.path not in ("/api/v1/health",):
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

        return response


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catch-all error handler that returns structured JSON errors."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        try:
            return await call_next(request)
        except Exception as e:
            logger.error("unhandled_error", path=request.url.path, error=str(e), error_type=type(e).__name__)
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error", "detail": str(e), "type": type(e).__name__},
            )


def setup_middleware(app: FastAPI) -> None:
    """Apply all middleware to the FastAPI app."""
    settings = get_settings()

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.util import get_remote_address

        limiter = Limiter(key_func=get_remote_address, default_limits=[settings.api.rate_limit])
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    except ImportError:
        logger.warning("slowapi_not_installed", message="Rate limiting disabled")

    # Custom middleware (order matters — last added runs first)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
