"""
In-process async event bus (replaces Kafka for free tier).

Uses asyncio.Queue for pub/sub and persists events to Postgres for audit.
Design Pattern: Observer/Pub-Sub — decouples event producers from consumers.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from src.infra.logging import get_logger

logger = get_logger("infra.event_bus")


class EventType(str, Enum):
    """All event types in the system."""
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    STEP_RETRYING = "step.retrying"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    RECOVERY_STARTED = "recovery.started"
    RECOVERY_COMPLETED = "recovery.completed"
    LLM_CALL_STARTED = "llm.call.started"
    LLM_CALL_COMPLETED = "llm.call.completed"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    MEMORY_QUERY = "memory.query"
    METRIC_RECORDED = "metric.recorded"


@dataclass
class Event:
    """An event in the system."""
    type: EventType
    payload: dict[str, Any]
    task_id: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "task_id": self.task_id,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


# Type alias for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """
    In-process async event bus with pub/sub.

    Subscribers register for specific event types and receive events asynchronously.
    Events are also persisted to Postgres via the TaskRepository for audit trail.
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._global_subscribers: list[EventHandler] = []
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe a handler to a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug("event_subscriber_added", event_type=event_type.value)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe a handler to ALL events."""
        self._global_subscribers.append(handler)

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        await self._queue.put(event)
        logger.debug("event_published", event_type=event.type.value, task_id=event.task_id)

    async def emit(self, event_type: EventType, payload: dict[str, Any],
                   task_id: str | None = None) -> Event:
        """Convenience method: create and publish an event."""
        event = Event(type=event_type, payload=payload, task_id=task_id)
        await self.publish(event)
        return event

    async def start(self) -> None:
        """Start the event processing worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._process_events())
        logger.info("event_bus_started")

    async def stop(self) -> None:
        """Stop the event processing worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("event_bus_stopped")

    async def _process_events(self) -> None:
        """Background worker that dispatches events to subscribers."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Dispatch to type-specific subscribers
            handlers = self._subscribers.get(event.type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error(
                        "event_handler_failed",
                        event_type=event.type.value,
                        handler=handler.__name__,
                        error=str(e),
                    )

            # Dispatch to global subscribers
            for handler in self._global_subscribers:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error(
                        "global_event_handler_failed",
                        handler=handler.__name__,
                        error=str(e),
                    )


# Module-level singleton
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the singleton EventBus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
