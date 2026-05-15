"""
Verifier — LLM-based post-action verification engine.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.infra.metrics import get_metrics
from src.llm.prompts import VERIFICATION_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import VerificationResult, parse_llm_response
from src.perception.multimodal_fuser import Evidence

logger = get_logger("verification.verifier")


class Verifier:
    """
    Post-action verification engine.

    After every action, evaluates whether the action succeeded using
    collected evidence and LLM reasoning.
    """

    async def verify(self, step_description: str, expected_outcome: str,
                      tool_name: str, evidence: list[Evidence]) -> VerificationResult:
        """Verify whether an action achieved its expected outcome."""
        # Format evidence for the prompt
        evidence_text = "\n".join(
            f"[{e.modality}] (confidence: {e.confidence:.2f}): {e.content[:500]}"
            for e in evidence
        )

        prompt = VERIFICATION_PROMPT.format(
            action_description=step_description,
            expected_outcome=expected_outcome,
            tool_name=tool_name,
            evidence=evidence_text,
        )

        provider = get_llm_provider()
        metrics = await get_metrics()

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
                max_tokens=500,
                temperature=0.1,
            )
            result = parse_llm_response(response.content, VerificationResult)
        except Exception as e:
            logger.error("verification_parse_failed", error=str(e))
            # Default: check if any evidence indicates error
            has_error = any(e.metadata.get("is_error") for e in evidence)
            result = VerificationResult(
                verified=not has_error,
                confidence=0.5,
                evidence_summary="Verification parse failed, using heuristic",
                reasoning=str(e),
            )

        # Record metric
        if result.confidence > 0.8:
            await metrics.record_verification("pass")
        elif result.confidence > 0.5:
            await metrics.record_verification("retry")
        else:
            await metrics.record_verification("rollback")

        logger.info(
            "verification_result",
            verified=result.verified,
            confidence=result.confidence,
            step=step_description[:50],
        )
        return result
