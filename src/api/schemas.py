"""
Pydantic request/response schemas for the API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AttachmentSchema(BaseModel):
    type: str = Field(description="Attachment type: image, audio, document")
    url: str = Field(default="", description="URL or base64 data")


class TaskCreateRequest(BaseModel):
    goal: str = Field(description="Natural language goal for the agent")
    attachments: list[AttachmentSchema] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict, description="Optional task config overrides")


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str = "queued"
    message: str = "Task created successfully"


class StepDetail(BaseModel):
    step_number: int
    description: str
    status: str
    tool: str | None = None
    tool_args: dict[str, Any] | None = None
    result: str | None = None
    verification: dict[str, Any] | None = None
    error: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    goal: str
    plan_summary: str | None = None
    total_steps: int = 0
    completed_steps: int = 0
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0
    created_at: datetime | None = None


class TaskFeedbackRequest(BaseModel):
    step_id: str | None = None
    feedback: str = Field(description="approve, reject, or modify")
    modification: str = ""


class MCPToolInfo(BaseModel):
    server: str
    name: str
    description: str
    parameters: dict[str, Any]


class MCPServerListResponse(BaseModel):
    servers: list[dict[str, Any]]
    tools: list[MCPToolInfo]
    total_tools: int


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "1.0.0"
    components: dict[str, str] = Field(default_factory=dict)


class MetricsResponse(BaseModel):
    summary: dict[str, Any]
    all_metrics: dict[str, float]


class WebSocketMessage(BaseModel):
    type: str  # step_started, step_completed, task_completed, error
    data: dict[str, Any] = Field(default_factory=dict)
    task_id: str = ""
    timestamp: datetime | None = None
