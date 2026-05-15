"""
Executor — selects and invokes MCP tools for plan steps.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.llm.prompts import TOOL_SELECTOR_PROMPT
from src.llm.provider import get_llm_provider
from src.llm.structured_output import ToolSelection, parse_llm_response
from src.mcp.client import MCPClient
from src.mcp.protocol import ToolResult

logger = get_logger("orchestrator.executor")


class Executor:
    """Selects tools and executes plan steps via the MCP client."""

    def __init__(self, mcp_client: MCPClient) -> None:
        self._mcp = mcp_client

    async def select_tool(self, step_description: str, expected_outcome: str,
                           context: str = "") -> ToolSelection:
        """Use LLM to select the best tool for a step."""
        provider = get_llm_provider()
        available_tools = self._mcp.get_tools_display()

        prompt = TOOL_SELECTOR_PROMPT.format(
            step_description=step_description,
            expected_outcome=expected_outcome,
            available_tools=available_tools,
            context=context or "No additional context.",
        )

        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=400,
        )

        try:
            selection = parse_llm_response(response.content, ToolSelection)
            logger.info("tool_selected", tool=selection.tool_name, reason=selection.reasoning[:50])
            return selection
        except ValueError:
            # Fallback: try to extract tool name from response
            return ToolSelection(tool_name="execute_command", arguments={"command": "echo 'fallback'"})

    async def execute(self, tool_selection: ToolSelection) -> ToolResult:
        """Execute a selected tool via the MCP client."""
        logger.info("executing_tool", tool=tool_selection.tool_name, args=str(tool_selection.arguments)[:100])
        return await self._mcp.call_tool(tool_selection.tool_name, tool_selection.arguments)

    async def execute_step(self, step_description: str, expected_outcome: str,
                            context: str = "") -> tuple[ToolSelection, ToolResult]:
        """Select and execute a tool for a step (convenience method)."""
        selection = await self.select_tool(step_description, expected_outcome, context)
        result = await self.execute(selection)
        return selection, result
