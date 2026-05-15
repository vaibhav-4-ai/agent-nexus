"""
MCP Server Registry — manages available servers and their tools.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.mcp.protocol import Tool
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.registry")


class ServerRegistry:
    """
    Registry of all available MCP servers and their tools.

    Auto-registers built-in servers on startup, tracks capabilities,
    and provides tool lookup for the orchestrator.
    """

    def __init__(self) -> None:
        self._servers: dict[str, BaseMCPServer] = {}
        self._tools_cache: dict[str, Tool] = {}

    async def register(self, server: BaseMCPServer) -> None:
        """Register and initialize an MCP server."""
        await server.initialize()
        self._servers[server.server_name] = server
        # Cache all tools
        for tool in server.list_tools():
            tool.server_name = server.server_name
            self._tools_cache[tool.name] = tool
        logger.info(
            "server_registered",
            server=server.server_name,
            tools=[t.name for t in server.list_tools()],
        )

    def get_server(self, name: str) -> BaseMCPServer | None:
        """Get a server by name."""
        return self._servers.get(name)

    def get_all_servers(self) -> dict[str, BaseMCPServer]:
        """Get all registered servers."""
        return dict(self._servers)

    def get_all_tools(self) -> list[Tool]:
        """Get all tools from all servers."""
        return list(self._tools_cache.values())

    def get_tool(self, tool_name: str) -> Tool | None:
        """Get a specific tool by name."""
        return self._tools_cache.get(tool_name)

    def find_server_for_tool(self, tool_name: str) -> BaseMCPServer | None:
        """Find which server provides a given tool."""
        tool = self._tools_cache.get(tool_name)
        if tool:
            return self._servers.get(tool.server_name)
        return None

    def get_tools_display(self) -> str:
        """Get a formatted string of all tools for LLM prompts."""
        lines = []
        for server_name, server in self._servers.items():
            lines.append(f"\n[{server_name}]")
            for tool in server.list_tools():
                lines.append(tool.to_display())
        return "\n".join(lines)

    def get_tools_for_api(self) -> list[dict[str, Any]]:
        """Get tools in API-friendly format."""
        result = []
        for server_name, server in self._servers.items():
            for tool in server.list_tools():
                result.append({
                    "server": server_name,
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.to_schema()["parameters"],
                })
        return result

    async def shutdown_all(self) -> None:
        """Shutdown all servers."""
        for server in self._servers.values():
            try:
                await server.shutdown()
            except Exception as e:
                logger.error("server_shutdown_error", server=server.server_name, error=str(e))
        self._servers.clear()
        self._tools_cache.clear()
        logger.info("all_servers_shutdown")
