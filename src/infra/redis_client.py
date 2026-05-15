"""
Async Redis client with Upstash support and in-memory fallback.

Uses Upstash serverless Redis (HTTP-based) in production.
Falls back to an in-memory dict when Redis is unavailable — Circuit Breaker pattern.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger

logger = get_logger("infra.redis")


class InMemoryCache:
    """Fallback in-memory cache when Redis is unavailable."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    async def get(self, key: str) -> str | None:
        if key in self._store:
            value, expires_at = self._store[key]
            if expires_at and time.time() > expires_at:
                del self._store[key]
                return None
            return value
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        expires_at = (time.time() + ex) if ex else None
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        val = await self.get(key)
        return val is not None

    async def keys(self, pattern: str = "*") -> list[str]:
        # Simple prefix matching only
        if pattern == "*":
            return list(self._store.keys())
        prefix = pattern.rstrip("*")
        return [k for k in self._store if k.startswith(prefix)]


class RedisClient:
    """
    Async Redis client with Upstash support and circuit breaker fallback.

    In production, connects to Upstash serverless Redis via HTTP.
    If Redis is unavailable or not configured, transparently falls back
    to an in-memory dictionary cache.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._fallback = InMemoryCache()
        self._using_fallback = False
        self._failure_count = 0
        self._max_failures = 3  # Circuit breaker threshold

    async def initialize(self) -> None:
        """Initialize the Redis connection."""
        settings = get_settings()

        if not settings.redis.enabled or not settings.redis.url:
            logger.info("redis_disabled", reason="No URL configured, using in-memory fallback")
            self._using_fallback = True
            return

        try:
            from upstash_redis.asyncio import Redis
            self._client = Redis(
                url=settings.redis.url,
                token=settings.redis.token.get_secret_value(),
            )
            # Test connection
            await self._client.ping()
            logger.info("redis_connected", provider="upstash")
        except Exception as e:
            logger.warning("redis_connection_failed", error=str(e), fallback="in-memory")
            self._using_fallback = True

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        if self._using_fallback:
            return await self._fallback.get(key)
        try:
            result = await self._client.get(key)
            self._failure_count = 0
            return result
        except Exception as e:
            self._handle_failure(e)
            return await self._fallback.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        """Set a value with optional TTL in seconds."""
        if self._using_fallback:
            await self._fallback.set(key, value, ex=ex)
            return
        try:
            if ex:
                await self._client.setex(key, ex, value)
            else:
                await self._client.set(key, value)
            self._failure_count = 0
        except Exception as e:
            self._handle_failure(e)
            await self._fallback.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        if self._using_fallback:
            await self._fallback.delete(key)
            return
        try:
            await self._client.delete(key)
            self._failure_count = 0
        except Exception as e:
            self._handle_failure(e)
            await self._fallback.delete(key)

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if self._using_fallback:
            return await self._fallback.exists(key)
        try:
            result = await self._client.exists(key)
            self._failure_count = 0
            return bool(result)
        except Exception as e:
            self._handle_failure(e)
            return await self._fallback.exists(key)

    async def cache_get(self, key: str) -> Any | None:
        """Get a JSON-serialized cached value."""
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def cache_set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Cache a value as JSON with TTL."""
        raw = json.dumps(value, default=str)
        await self.set(key, raw, ex=ttl)

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Publish a message to a channel (no-op if using fallback)."""
        if self._using_fallback:
            return
        try:
            await self._client.publish(channel, json.dumps(message, default=str))
        except Exception as e:
            logger.warning("redis_publish_failed", channel=channel, error=str(e))

    def _handle_failure(self, error: Exception) -> None:
        """Circuit breaker: switch to fallback after repeated failures."""
        self._failure_count += 1
        logger.warning(
            "redis_operation_failed",
            error=str(error),
            failure_count=self._failure_count,
        )
        if self._failure_count >= self._max_failures:
            logger.error("redis_circuit_breaker_open", switching_to="in-memory fallback")
            self._using_fallback = True

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client and hasattr(self._client, "close"):
            await self._client.close()
        logger.info("redis_closed")


# Module-level singleton
_redis_client: RedisClient | None = None


async def get_redis() -> RedisClient:
    """Get the singleton Redis client, initializing if needed."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
        await _redis_client.initialize()
    return _redis_client


async def close_redis() -> None:
    """Close the Redis client."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
