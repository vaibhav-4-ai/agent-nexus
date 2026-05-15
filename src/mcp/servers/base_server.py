"""
Base MCP Server — Template Method pattern.

All built-in MCP servers extend this class, implementing list_tools() and call_tool().
Common error handling, logging, and lifecycle management is handled here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.infra.logging import Timer, get_logger
from src.mcp.protocol import Tool, ToolResult

logger = get_logger("mcp.server")


class BaseMCPServer(ABC):
    """
    Abstract base for all MCP servers.

    Subclasses implement:
      - server_name: str property
      - list_tools() -> list[Tool]
      - _execute_tool(name, args) -> ToolResult
    """

    @property
    @abstractmethod
    def server_name(self) -> str:
        """Unique name identifying this server."""
        ...

    @abstractmethod
    def list_tools(self) -> list[Tool]:
        """List all tools this server provides."""
        ...

    @abstractmethod
    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool. Implemented by subclasses."""
        ...

    async def initialize(self) -> None:
        """Optional initialization hook. Override if needed."""
        logger.info("mcp_server_initialized", server=self.server_name)

    async def shutdown(self) -> None:
        """Optional shutdown hook. Override if needed."""
        logger.info("mcp_server_shutdown", server=self.server_name)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Call a tool with validation, error handling, and logging.

        This is the public method — it wraps _execute_tool with common concerns.
        """
        # Validate tool exists
        available = {t.name for t in self.list_tools()}
        if tool_name not in available:
            return ToolResult(
                content="",
                is_error=True,
                error_message=f"Tool '{tool_name}' not found on server '{self.server_name}'. "
                              f"Available: {sorted(available)}",
            )

        # Execute with timing and error handling
        with Timer(logger, "tool_execution", server=self.server_name, tool=tool_name):
            try:
                result = await self._execute_tool(tool_name, arguments)
                return result
            except TimeoutError:
                return ToolResult(
                    content="", is_error=True,
                    error_message=f"Tool '{tool_name}' timed out",
                )
            except Exception as e:
                logger.error(
                    "tool_execution_error",
                    server=self.server_name, tool=tool_name, error=str(e),
                )
                return ToolResult(
                    content="", is_error=True,
                    error_message=f"Tool execution failed: {type(e).__name__}: {e}",
                )
