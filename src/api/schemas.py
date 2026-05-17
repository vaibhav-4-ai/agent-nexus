"""
Pydantic request/response schemas for the API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class AttachmentSchema(BaseModel):
    type: str = Field(description="Attachment type: image, audio, document")
    url: str = Field(default="", description="URL or base64 data")


class BYOKConfig(BaseModel):
    """Per-request inference credential override.

    When present on a TaskCreateRequest, the server routes that single task's
    LLM calls through the supplied provider + model + key, bypassing the
    server's default credentials. The key is never persisted server-side and
    never written to logs (redaction + omission at call sites).
    """
    provider: Literal["groq", "openai", "anthropic", "gemini"] = Field(
        description="Inference provider identifier."
    )
    model: str = Field(
        description=("Fully qualified model identifier as accepted by LiteLLM, "
                     "e.g. 'openai/gpt-4o-mini', 'anthropic/claude-3-5-haiku-latest', "
                     "'gemini/gemini-1.5-flash'."),
        min_length=1, max_length=200,
    )
    api_key: SecretStr = Field(
        description="Provider API key. Scoped to this request only; never persisted.",
        min_length=10, max_length=500,
    )


class TaskCreateRequest(BaseModel):
    goal: str = Field(description="Natural language goal for the agent")
    attachments: list[AttachmentSchema] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict, description="Optional task config overrides")
    byok: BYOKConfig | None = Field(
        default=None,
        description="Optional per-request inference credentials (BYOK). When omitted, "
                    "the server's configured credentials are used.",
    )


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
