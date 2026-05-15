"""
Planner — LLM-powered execution plan generation.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.llm.prompts import PLANNER_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import ExecutionPlan, ParsedGoal, parse_llm_response, build_retry_prompt

logger = get_logger("orchestrator.planner")


class Planner:
    """Creates and updates execution plans using LLM reasoning."""

    async def create_plan(self, parsed_goal: ParsedGoal, available_tools: str,
                           past_experience: str = "", context: str = "") -> ExecutionPlan:
        """Create an execution plan from a parsed goal."""
        provider = get_llm_provider()

        prompt = PLANNER_PROMPT.format(
            available_tools=available_tools,
            past_experience=past_experience or "No past experience available.",
            context=context or "Starting fresh.",
        )

        user_message = (
            f"Objective: {parsed_goal.objective}\n"
            f"Constraints: {', '.join(parsed_goal.constraints) or 'None'}\n"
            f"Success criteria: {', '.join(parsed_goal.success_criteria) or 'None'}\n"
            f"Complexity: {parsed_goal.complexity}\n"
            f"Required modalities: {', '.join(parsed_goal.required_modalities) or 'Any'}"
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message},
        ]

        for attempt in range(2):
            try:
                response = await provider.complete(messages=messages, json_mode=True, max_tokens=1500)
                plan = parse_llm_response(response.content, ExecutionPlan)
                logger.info("plan_created", steps=len(plan.steps), summary=plan.plan_summary[:80])
                return plan
            except ValueError as e:
                if attempt == 0:
                    messages.append({"role": "user", "content": build_retry_prompt("", str(e))})
                else:
                    raise

        return ExecutionPlan(plan_summary="Direct execution", steps=[])

    async def replan_step(self, failed_step_description: str, failure_reason: str,
                           available_tools: str, context: str) -> ExecutionPlan:
        """Re-plan a failed step with a different approach."""
        provider = get_llm_provider()

        prompt = (
            f"A step in the plan failed. Create an alternative approach.\n\n"
            f"Failed step: {failed_step_description}\n"
            f"Failure reason: {failure_reason}\n\n"
            f"Available tools:\n{available_tools}\n\n"
            f"Context:\n{context}\n\n"
            f"Create 1-3 alternative steps to accomplish the same goal differently.\n"
            f"Respond in JSON with the same plan format."
        )

        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=800,
        )
        return parse_llm_response(response.content, ExecutionPlan)
