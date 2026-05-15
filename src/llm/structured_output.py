"""
Structured output parsing for LLM responses.

Validates LLM JSON output against Pydantic models with retry-on-parse-failure.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from src.infra.logging import get_logger

logger = get_logger("llm.structured_output")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Pydantic Models for LLM Outputs
# ---------------------------------------------------------------------------
class ParsedGoal(BaseModel):
    """Structured output from goal parsing."""
    objective: str
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    required_modalities: list[str] = Field(default_factory=list)
    complexity: str = "medium"
    estimated_steps: int = 5


class PlanStep(BaseModel):
    """A single step in an execution plan."""
    step_number: int
    description: str
    tool: str = ""
    expected_args: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: str = ""
    depends_on: list[int] = Field(default_factory=list)
    fallback_strategy: str = ""


class ExecutionPlan(BaseModel):
    """Structured execution plan from the planner."""
    plan_summary: str
    steps: list[PlanStep]
    estimated_total_time: str = ""
    risk_factors: list[str] = Field(default_factory=list)


class ToolSelection(BaseModel):
    """Tool selection result."""
    tool_name: str
    server_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


class VerificationResult(BaseModel):
    """Verification engine result."""
    verified: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_summary: str = ""
    reasoning: str = ""
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class RecoveryDecision(BaseModel):
    """Recovery strategy decision."""
    strategy: str  # retry, rollback, skip, escalate
    reasoning: str = ""
    modifications: dict[str, Any] = Field(default_factory=dict)
    root_cause: str = ""


class TaskSummary(BaseModel):
    """Task completion summary."""
    summary: str
    key_findings: list[str] = Field(default_factory=list)
    issues_encountered: list[str] = Field(default_factory=list)
    final_result: str = ""
    recommendations: list[str] = Field(default_factory=list)


class ClaimVerification(BaseModel):
    """Individual claim verification."""
    claim: str
    verified: bool
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: str = ""
    reasoning: str = ""


class ClaimCheckResult(BaseModel):
    """Full claim check result."""
    claims: list[ClaimVerification] = Field(default_factory=list)
    overall_reliability: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# Parsing Utilities
# ---------------------------------------------------------------------------
def extract_json(text: str) -> str:
    """
    Extract JSON from LLM response text that may contain markdown code fences.

    Handles:
      - Pure JSON
      - ```json ... ```
      - ``` ... ```
      - JSON embedded in text
    """
    text = text.strip()

    # Try to find JSON in code fences
    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # Inside code fence
                # Remove language identifier (e.g., 'json')
                lines = part.strip().split("\n")
                if lines and lines[0].strip().lower() in ("json", "jsonc", ""):
                    lines = lines[1:]
                candidate = "\n".join(lines).strip()
                if candidate:
                    return candidate

    # Try to find JSON object/array in the text
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        end_idx = text.rfind(end_char)
        if start_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx + 1]

    return text


def parse_llm_response(response_text: str, model_class: type[T]) -> T:
    """
    Parse LLM response text into a Pydantic model.

    Extracts JSON from the response, validates against the model,
    and returns a typed instance.

    Raises:
        ValueError: If parsing fails after extraction.
    """
    json_str = extract_json(response_text)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("json_parse_failed", error=str(e), raw_text=response_text[:200])
        raise ValueError(f"Failed to parse JSON from LLM response: {e}") from e

    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        logger.error(
            "pydantic_validation_failed",
            model=model_class.__name__,
            errors=str(e),
        )
        raise ValueError(f"LLM response validation failed for {model_class.__name__}: {e}") from e


def build_retry_prompt(original_prompt: str, error_message: str) -> str:
    """Build a retry prompt that includes the parse error for self-correction."""
    return f"""{original_prompt}

IMPORTANT: Your previous response could not be parsed. Error:
{error_message}

Please respond with ONLY valid JSON, no markdown, no explanation. Just the JSON object."""
