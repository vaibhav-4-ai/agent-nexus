"""
Goal Parser — LLM-powered goal decomposition.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.llm.prompts import GOAL_PARSER_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import ParsedGoal, parse_llm_response, build_retry_prompt

logger = get_logger("orchestrator.goal_parser")


class GoalParser:
    """Parses user goals into structured, actionable objectives."""

    async def parse(self, goal: str, attachments: list[dict[str, Any]] | None = None) -> ParsedGoal:
        """Parse a natural language goal into structured format."""
        provider = get_llm_provider()

        user_message = f"User goal: {goal}"
        if attachments:
            attachment_desc = ", ".join(
                f"{a.get('type', 'unknown')} attachment" for a in attachments
            )
            user_message += f"\n\nAttachments provided: {attachment_desc}"

        messages = [
            {"role": "system", "content": GOAL_PARSER_PROMPT},
            {"role": "user", "content": user_message},
        ]

        # Try parsing with retry on failure
        for attempt in range(2):
            try:
                response = await provider.complete(messages=messages, json_mode=True, max_tokens=500)
                parsed = parse_llm_response(response.content, ParsedGoal)
                logger.info("goal_parsed", objective=parsed.objective[:80], complexity=parsed.complexity)
                return parsed
            except ValueError as e:
                if attempt == 0:
                    messages.append({"role": "user", "content": build_retry_prompt("", str(e))})
                    logger.warning("goal_parse_retry", error=str(e))
                else:
                    raise

        # Fallback — should not reach here
        return ParsedGoal(objective=goal, complexity="medium", estimated_steps=5)
