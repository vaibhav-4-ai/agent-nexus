"""
Evidence Collector — gathers multimodal evidence after each action.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.mcp.protocol import ToolResult
from src.perception.multimodal_fuser import Evidence

logger = get_logger("verification.evidence")


class EvidenceCollector:
    """Collects evidence from tool results for verification."""

    async def collect(self, tool_name: str, tool_result: ToolResult,
                       step_description: str = "") -> list[Evidence]:
        """Collect evidence from a tool execution result."""
        evidence_list: list[Evidence] = []

        # Primary evidence: the tool's output
        evidence_list.append(Evidence(
            modality="text",
            content=tool_result.content[:2000] if tool_result.content else "(empty output)",
            confidence=0.0 if tool_result.is_error else 0.9,
            metadata={"tool": tool_name, "is_error": tool_result.is_error},
        ))

        # Error evidence
        if tool_result.is_error:
            evidence_list.append(Evidence(
                modality="text",
                content=f"Error: {tool_result.error_message}",
                confidence=0.95,
                metadata={"type": "error", "tool": tool_name},
            ))

        # Artifact evidence (e.g., screenshots)
        for artifact in tool_result.artifacts:
            if artifact.get("type") == "image":
                evidence_list.append(Evidence(
                    modality="visual",
                    content=f"Screenshot captured from {tool_name}",
                    confidence=0.8,
                    metadata={"type": "screenshot", "format": artifact.get("format", "png")},
                ))

        # Metadata evidence
        if tool_result.metadata:
            meta_summary = ", ".join(f"{k}={v}" for k, v in tool_result.metadata.items())
            evidence_list.append(Evidence(
                modality="text",
                content=f"Metadata: {meta_summary}",
                confidence=0.85,
                metadata={"type": "metadata"},
            ))

        logger.debug("evidence_collected", tool=tool_name, evidence_count=len(evidence_list))
        return evidence_list
