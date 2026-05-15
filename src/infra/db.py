"""
Async PostgreSQL database layer using SQLAlchemy.

Connects to Neon.tech (serverless Postgres) in production or local Postgres in dev.
Implements the Repository pattern for clean data access.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from sqlalchemy import (
    JSON, DateTime, Float, Index, Integer, String, Text, func, select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import get_settings
from src.infra.logging import get_logger

logger = get_logger("infra.db")


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PARSING = "parsing"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------
class TaskModel(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING.value, index=True)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_chain: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    total_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


class TaskStepModel(Base):
    __tablename__ = "task_steps"
    __table_args__ = (Index("ix_task_steps_task_id", "task_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=StepStatus.PENDING.value)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventLogModel(Base):
    __tablename__ = "event_log"
    __table_args__ = (Index("ix_event_log_task_id", "task_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentMetricModel(Base):
    __tablename__ = "agent_metrics"
    __table_args__ = (Index("ix_agent_metrics_name_ts", "metric_name", "timestamp"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    labels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeGraphModel(Base):
    __tablename__ = "knowledge_graph"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    relations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Engine & Session Factory
# ---------------------------------------------------------------------------
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database.url,
            pool_size=settings.database.pool_size,
            pool_pre_ping=settings.database.pool_pre_ping,
            echo=settings.database.echo,
        )
        logger.info("database_engine_created", url=settings.database.url.split("@")[-1])
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(bind=_get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_initialized", tables=list(Base.metadata.tables.keys()))


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database_closed")


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
class TaskRepository:
    """Repository pattern for Task CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, goal: str, attachments: list[dict] | None = None, config: dict | None = None) -> TaskModel:
        task = TaskModel(goal=goal, attachments=attachments, config=config, status=TaskStatus.PENDING.value)
        self._session.add(task)
        await self._session.flush()
        logger.info("task_created", task_id=task.id, goal=goal[:100])
        return task

    async def get(self, task_id: str) -> TaskModel | None:
        result = await self._session.execute(select(TaskModel).where(TaskModel.id == task_id))
        return result.scalar_one_or_none()

    async def update_status(self, task_id: str, status: TaskStatus, **kwargs: Any) -> TaskModel | None:
        task = await self.get(task_id)
        if task is None:
            return None
        task.status = status.value
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(timezone.utc)
        await self._session.flush()
        logger.info("task_status_updated", task_id=task_id, status=status.value)
        return task

    async def list_tasks(self, limit: int = 50, offset: int = 0, status: TaskStatus | None = None) -> list[TaskModel]:
        query = select(TaskModel).order_by(TaskModel.created_at.desc())
        if status:
            query = query.where(TaskModel.status == status.value)
        result = await self._session.execute(query.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def add_step(self, task_id: str, step_number: int, description: str) -> TaskStepModel:
        step = TaskStepModel(task_id=task_id, step_number=step_number, description=description)
        self._session.add(step)
        await self._session.flush()
        return step

    async def update_step(self, step_id: str, **kwargs: Any) -> TaskStepModel | None:
        result = await self._session.execute(select(TaskStepModel).where(TaskStepModel.id == step_id))
        step = result.scalar_one_or_none()
        if step is None:
            return None
        for key, value in kwargs.items():
            if hasattr(step, key):
                setattr(step, key, value)
        await self._session.flush()
        return step

    async def get_steps(self, task_id: str) -> list[TaskStepModel]:
        result = await self._session.execute(
            select(TaskStepModel).where(TaskStepModel.task_id == task_id).order_by(TaskStepModel.step_number)
        )
        return list(result.scalars().all())

    async def log_event(self, event_type: str, payload: dict, task_id: str | None = None) -> None:
        self._session.add(EventLogModel(task_id=task_id, event_type=event_type, payload=payload))
        await self._session.flush()

    async def record_metric(self, metric_name: str, value: float, labels: dict | None = None) -> None:
        self._session.add(AgentMetricModel(metric_name=metric_name, metric_value=value, labels=labels))
        await self._session.flush()

    async def get_metrics(self, metric_name: str, limit: int = 100) -> list[AgentMetricModel]:
        result = await self._session.execute(
            select(AgentMetricModel).where(AgentMetricModel.metric_name == metric_name)
            .order_by(AgentMetricModel.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Background Pruning
# ---------------------------------------------------------------------------
async def prune_old_data(days_to_keep: int = 3) -> None:
    """Delete records older than days_to_keep to preserve free tier limits."""
    from datetime import timedelta
    from sqlalchemy import delete
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
    engine = _get_engine()
    
    try:
        async with engine.begin() as conn:
            res_events = await conn.execute(delete(EventLogModel).where(EventLogModel.created_at < cutoff_date))
            res_steps = await conn.execute(delete(TaskStepModel).where(TaskStepModel.created_at < cutoff_date))
            res_tasks = await conn.execute(delete(TaskModel).where(TaskModel.created_at < cutoff_date))
            res_metrics = await conn.execute(delete(AgentMetricModel).where(AgentMetricModel.timestamp < cutoff_date))
            
            logger.info("database_pruned", 
                        events_deleted=res_events.rowcount,
                        steps_deleted=res_steps.rowcount,
                        tasks_deleted=res_tasks.rowcount,
                        metrics_deleted=res_metrics.rowcount)
    except Exception as e:
        logger.error("database_pruning_failed", error=str(e))
