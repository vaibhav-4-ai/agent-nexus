"""
HTTP MCP Server — make HTTP requests and fetch web pages.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer


class HTTPServer(BaseMCPServer):
    """MCP server for making HTTP requests."""

    @property
    def server_name(self) -> str:
        return "http_client"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        await super().initialize()

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        await super().shutdown()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="http_request", server_name=self.server_name,
                description="Make an HTTP request (GET, POST, PUT, DELETE).",
                parameters=[
                    ToolParameter("method", "string", "HTTP method (GET, POST, PUT, DELETE)"),
                    ToolParameter("url", "string", "The URL to request"),
                    ToolParameter("headers", "object", "Request headers", required=False),
                    ToolParameter("body", "string", "Request body (for POST/PUT)", required=False),
                ],
            ),
            Tool(
                name="fetch_webpage", server_name=self.server_name,
                description="Fetch a webpage and return its text content.",
                parameters=[ToolParameter("url", "string", "URL to fetch")],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        assert self._client is not None
        match tool_name:
            case "http_request":
                return await self._http_request(arguments)
            case "fetch_webpage":
                return await self._fetch_webpage(arguments["url"])
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown tool: {tool_name}")

    async def _http_request(self, args: dict[str, Any]) -> ToolResult:
        assert self._client is not None
        method = args["method"].upper()
        url = args["url"]
        headers = args.get("headers", {})
        body = args.get("body")

        try:
            response = await self._client.request(method, url, headers=headers, content=body)
            content_type = response.headers.get("content-type", "")

            if "json" in content_type:
                import json
                body_text = json.dumps(response.json(), indent=2)
            else:
                body_text = response.text[:10000]  # Limit response size

            return ToolResult(
                content=f"Status: {response.status_code}\n\n{body_text}",
                is_error=response.status_code >= 400,
                metadata={"status_code": response.status_code, "url": url, "method": method},
            )
        except httpx.RequestError as e:
            return ToolResult(content="", is_error=True, error_message=f"Request failed: {e}")

    async def _fetch_webpage(self, url: str) -> ToolResult:
        assert self._client is not None
        try:
            response = await self._client.get(url)
            # Simple HTML to text conversion
            text = response.text
            import re
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return ToolResult(
                content=text[:10000],
                metadata={"url": url, "status_code": response.status_code},
            )
        except httpx.RequestError as e:
            return ToolResult(content="", is_error=True, error_message=f"Failed to fetch: {e}")
