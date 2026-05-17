"""
API Middleware — CORS, rate limiting, request ID, logging, error handling, auth.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import Environment, get_settings
from src.infra.logging import bind_request_context, get_logger

logger = get_logger("api.middleware")


# ---------------------------------------------------------------------------
# API-key dependency (S1)
# ---------------------------------------------------------------------------
async def require_api_key(request: Request) -> None:
    """FastAPI dependency that enforces X-API-Key on mutating endpoints.

    Behavior:
    - If `api.api_key` setting is non-empty: header must match. 401 otherwise.
    - If `api.api_key` is empty AND environment is PRODUCTION: fail closed (503).
    - If `api.api_key` is empty AND environment is DEV/STAGING: allow (lab use).
    """
    settings = get_settings()
    configured = settings.api.api_key.get_secret_value()
    if not configured:
        if settings.environment == Environment.PRODUCTION:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API_KEY must be configured for production environments",
            )
        return  # Dev mode: skip auth for ergonomics
    supplied = request.headers.get("X-API-Key", "")
    if supplied != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


def is_valid_ws_api_key(api_key: str | None) -> bool:
    """Auth helper for WebSocket endpoints (which can't easily use Depends).

    Returns True if request is authorized (either configured key matches, OR
    no key configured in dev mode).
    """
    settings = get_settings()
    configured = settings.api.api_key.get_secret_value()
    if not configured:
        return settings.environment != Environment.PRODUCTION
    return (api_key or "") == configured


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Adds a unique request ID to every request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        request_id = str(uuid.uuid4())[:8]
        bind_request_context(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _client_ip(request: Request) -> str:
    """Return the originating client IP, honoring X-Forwarded-For when present
    (HF Spaces and most reverse proxies set it). Falls back to the direct peer."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        # X-Forwarded-For is a comma-separated list; the first entry is the original client.
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with duration + client IP."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # Skip health check spam
        if request.url.path not in ("/api/v1/health",):
            logger.info(
                "http_request",
                method=request.method,
                # L3-C: path only — query string deliberately omitted (it could
                # contain ?api_key=... or other URL-borne secrets).
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration_ms, 2),
                # L2: log who made the request so abuse can be traced.
                client_ip=_client_ip(request),
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

    # CORS (S2 — hardened):
    # - Default cors_origins is now [] (no cross-origin requests).
    # - If user explicitly sets ["*"], force allow_credentials=False per spec
    #   (browsers reject "*" + credentials anyway, but this avoids the warning).
    # - allow_methods / allow_headers are restricted (no longer "*").
    origins = settings.api.cors_origins or []
    wildcard = origins == ["*"]
    if wildcard:
        logger.warning(
            "cors_wildcard_enabled",
            hint="API_CORS_ORIGINS=['*'] disables credentialed cross-origin requests. "
                 "Set a specific origin list for production.",
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not wildcard,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
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
