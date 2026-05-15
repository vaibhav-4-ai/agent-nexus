"""
MCP Client — discover and invoke tools across all registered servers.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import Timer, get_logger
from src.infra.metrics import get_metrics
from src.mcp.protocol import Tool, ToolResult
from src.mcp.registry import ServerRegistry

logger = get_logger("mcp.client")


class MCPClient:
    """
    Central client for invoking MCP tools.

    Routes tool calls to the correct server, handles errors,
    and records metrics for every invocation.
    """

    def __init__(self, registry: ServerRegistry) -> None:
        self._registry = registry

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Call an MCP tool by name with the given arguments.

        Automatically routes to the correct server based on the tool registry.
        """
        server = self._registry.find_server_for_tool(tool_name)
        if server is None:
            return ToolResult(
                content="",
                is_error=True,
                error_message=f"No server found for tool '{tool_name}'. "
                              f"Available tools: {[t.name for t in self._registry.get_all_tools()]}",
            )

        metrics = await get_metrics()

        with Timer(logger, "mcp_tool_call", server=server.server_name, tool=tool_name) as timer:
            result = await server.call_tool(tool_name, arguments)

        # Record metrics
        await metrics.record_tool_call(
            server=server.server_name,
            tool=tool_name,
            success=not result.is_error,
            duration_ms=timer.duration_ms,
        )

        if result.is_error:
            logger.warning(
                "tool_call_error",
                tool=tool_name,
                server=server.server_name,
                error=result.error_message,
            )

        return result

    def list_all_tools(self) -> list[Tool]:
        """List all available tools across all servers."""
        return self._registry.get_all_tools()

    def get_tools_display(self) -> str:
        """Get formatted tool list for LLM prompts."""
        return self._registry.get_tools_display()

    async def initialize_all_servers(self) -> None:
        """Initialize all built-in MCP servers."""
        from src.mcp.servers.filesystem_server import FilesystemServer
        from src.mcp.servers.shell_server import ShellServer
        from src.mcp.servers.http_server import HTTPServer
        from src.mcp.servers.database_server import DatabaseServer
        from src.mcp.servers.code_exec_server import CodeExecServer
        from src.mcp.servers.search_server import SearchServer
        from src.mcp.servers.browser_server import BrowserServer

        servers = [
            FilesystemServer(),
            ShellServer(),
            HTTPServer(),
            DatabaseServer(),
            CodeExecServer(),
            SearchServer(),
            BrowserServer(),
        ]

        for server in servers:
            try:
                await self._registry.register(server)
            except Exception as e:
                logger.error(
                    "server_registration_failed",
                    server=server.server_name,
                    error=str(e),
                )

        logger.info(
            "mcp_client_ready",
            total_servers=len(self._registry.get_all_servers()),
            total_tools=len(self._registry.get_all_tools()),
        )
