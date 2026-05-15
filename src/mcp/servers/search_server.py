"""
Search MCP Server — web search via Tavily or DuckDuckGo.
"""

from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.search")


class SearchServer(BaseMCPServer):
    """MCP server for web search (Tavily or DuckDuckGo fallback)."""

    @property
    def server_name(self) -> str:
        return "search"

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="web_search", server_name=self.server_name,
                description="Search the web and return relevant results.",
                parameters=[
                    ToolParameter("query", "string", "Search query"),
                    ToolParameter("max_results", "integer", "Max results to return", required=False, default=5),
                ],
            ),
            Tool(
                name="news_search", server_name=self.server_name,
                description="Search for recent news articles.",
                parameters=[
                    ToolParameter("query", "string", "News search query"),
                    ToolParameter("max_results", "integer", "Max results", required=False, default=5),
                ],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        query = arguments["query"]
        max_results = arguments.get("max_results", 5)

        settings = get_settings()
        tavily_key = settings.mcp.tavily_api_key.get_secret_value()

        if tavily_key and settings.mcp.search_provider == "tavily":
            return await self._tavily_search(query, max_results, tavily_key, tool_name == "news_search")
        else:
            return await self._duckduckgo_search(query, max_results, tool_name == "news_search")

    async def _tavily_search(self, query: str, max_results: int,
                              api_key: str, news: bool) -> ToolResult:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "topic": "news" if news else "general",
                    },
                    timeout=15.0,
                )
                data = response.json()

            results = data.get("results", [])
            formatted = []
            for r in results:
                formatted.append(f"**{r.get('title', 'No title')}**\n{r.get('url', '')}\n{r.get('content', '')[:300]}\n")

            return ToolResult(
                content=f"Search results for '{query}':\n\n" + "\n---\n".join(formatted) if formatted else "No results found.",
                metadata={"source": "tavily", "result_count": len(results)},
            )
        except Exception as e:
            logger.warning("tavily_search_failed", error=str(e), fallback="duckduckgo")
            return await self._duckduckgo_search(query, max_results, news)

    async def _duckduckgo_search(self, query: str, max_results: int, news: bool) -> ToolResult:
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                if news:
                    raw_results = list(ddgs.news(query, max_results=max_results))
                else:
                    raw_results = list(ddgs.text(query, max_results=max_results))

            formatted = []
            for r in raw_results:
                title = r.get("title", "No title")
                url = r.get("href", r.get("url", ""))
                body = r.get("body", r.get("excerpt", ""))[:300]
                formatted.append(f"**{title}**\n{url}\n{body}\n")

            return ToolResult(
                content=f"Search results for '{query}':\n\n" + "\n---\n".join(formatted) if formatted else "No results found.",
                metadata={"source": "duckduckgo", "result_count": len(raw_results)},
            )
        except Exception as e:
            return ToolResult(content="", is_error=True, error_message=f"Search failed: {e}")
