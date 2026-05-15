"""
Multimodal Fuser — combine evidence from multiple perception engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.infra.logging import get_logger
from src.llm.provider import get_llm_provider

logger = get_logger("perception.fuser")


@dataclass
class Evidence:
    """A piece of evidence from a perception engine."""
    modality: str  # "visual", "text", "code", "audio", "metrics"
    content: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FusedEvidence:
    """Combined evidence from multiple modalities."""
    summary: str
    overall_confidence: float
    individual_evidence: list[Evidence]
    conflicts: list[str] = field(default_factory=list)


class MultimodalFuser:
    """Fuses evidence from multiple perception engines using LLM synthesis."""

    async def fuse(self, evidence_list: list[Evidence]) -> FusedEvidence:
        """
        Combine multiple pieces of evidence into a unified assessment.

        Uses LLM to synthesize when multiple modalities provide conflicting or
        complementary information.
        """
        if not evidence_list:
            return FusedEvidence(summary="No evidence collected.", overall_confidence=0.0, individual_evidence=[])

        if len(evidence_list) == 1:
            e = evidence_list[0]
            return FusedEvidence(
                summary=e.content,
                overall_confidence=e.confidence,
                individual_evidence=[e],
            )

        # Build a prompt for LLM synthesis
        evidence_parts = []
        for i, e in enumerate(evidence_list, 1):
            evidence_parts.append(
                f"Evidence {i} [{e.modality}] (confidence: {e.confidence:.2f}):\n{e.content[:500]}"
            )

        prompt = (
            "You are analyzing multiple pieces of evidence from different modalities.\n"
            "Synthesize them into a unified assessment.\n\n"
            + "\n\n".join(evidence_parts) +
            "\n\nRespond in JSON: {\"summary\": \"...\", \"overall_confidence\": 0.0-1.0, "
            "\"conflicts\": [\"any contradictions between evidence\"]}"
        )

        provider = get_llm_provider()
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=500,
        )

        try:
            import json
            result = json.loads(response.content)
            return FusedEvidence(
                summary=result.get("summary", response.content),
                overall_confidence=result.get("overall_confidence", 0.7),
                individual_evidence=evidence_list,
                conflicts=result.get("conflicts", []),
            )
        except Exception:
            # If JSON parsing fails, use the raw response
            avg_conf = sum(e.confidence for e in evidence_list) / len(evidence_list)
            return FusedEvidence(
                summary=response.content,
                overall_confidence=avg_conf,
                individual_evidence=evidence_list,
            )
