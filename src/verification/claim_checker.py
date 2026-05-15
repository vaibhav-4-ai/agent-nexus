"""
Claim Checker — decompose and verify individual claims from LLM output.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.llm.prompts import CLAIM_CHECKER_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import ClaimCheckResult, parse_llm_response
from src.perception.multimodal_fuser import Evidence

logger = get_logger("verification.claims")


class ClaimChecker:
    """Decomposes LLM text claims into individually verifiable statements."""

    async def check_claims(self, text: str, evidence: list[Evidence]) -> ClaimCheckResult:
        """Decompose text into claims and verify each against evidence."""
        evidence_text = "\n".join(f"[{e.modality}]: {e.content[:300]}" for e in evidence)

        prompt = CLAIM_CHECKER_PROMPT.format(text=text[:1000], evidence=evidence_text)

        provider = get_llm_provider()
        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
                max_tokens=800,
            )
            return parse_llm_response(response.content, ClaimCheckResult)
        except Exception as e:
            logger.error("claim_check_failed", error=str(e))
            return ClaimCheckResult(claims=[], overall_reliability=0.5)
