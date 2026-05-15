"""
Recovery Engine — self-healing via rollback, retry, and re-planning.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.infra.metrics import get_metrics
from src.llm.prompts import RECOVERY_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import RecoveryDecision, VerificationResult, parse_llm_response

logger = get_logger("verification.recovery")


class RecoveryEngine:
    """
    Self-healing recovery engine.

    When verification fails, decides the recovery strategy:
    retry, rollback, skip, or escalate.
    """

    MAX_RETRIES = 3

    async def decide(self, step_description: str, tool_name: str,
                      evidence_summary: str, verification: VerificationResult,
                      retry_count: int) -> RecoveryDecision:
        """Decide the recovery strategy for a failed step."""
        metrics = await get_metrics()

        # Fast path: if we've exceeded max retries, escalate
        if retry_count >= self.MAX_RETRIES:
            await metrics.increment("agent_recovery_attempts", labels={"strategy": "escalate"})
            return RecoveryDecision(
                strategy="escalate",
                reasoning=f"Max retries ({self.MAX_RETRIES}) exceeded",
                root_cause="Persistent failure after multiple attempts",
            )

        # Use LLM to decide recovery strategy
        prompt = RECOVERY_PROMPT.format(
            step_description=step_description,
            tool_name=tool_name,
            evidence=evidence_summary,
            verification=f"verified={verification.verified}, confidence={verification.confidence}, "
                         f"issues={verification.issues}",
            retry_count=retry_count,
        )

        provider = get_llm_provider()
        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                json_mode=True,
                max_tokens=400,
                temperature=0.1,
            )
            decision = parse_llm_response(response.content, RecoveryDecision)
        except Exception as e:
            logger.error("recovery_decision_failed", error=str(e))
            # Default: retry if first failure, rollback if second+
            decision = RecoveryDecision(
                strategy="retry" if retry_count < 2 else "rollback",
                reasoning=f"Default recovery (parse failed: {e})",
            )

        # Validate strategy
        valid_strategies = {"retry", "rollback", "skip", "escalate"}
        if decision.strategy not in valid_strategies:
            decision.strategy = "retry"

        await metrics.increment("agent_recovery_attempts", labels={"strategy": decision.strategy})
        logger.info(
            "recovery_decision",
            strategy=decision.strategy,
            retry_count=retry_count,
            step=step_description[:50],
        )
        return decision
