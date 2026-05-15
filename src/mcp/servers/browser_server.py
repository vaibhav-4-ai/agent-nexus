"""
Browser MCP Server — Playwright-based browser control.

Optional server (disabled by default on HF Spaces due to RAM constraints).
"""

from __future__ import annotations

import base64
from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.browser")


class BrowserServer(BaseMCPServer):
    """MCP server for Playwright browser automation."""

    @property
    def server_name(self) -> str:
        return "browser"

    def __init__(self) -> None:
        self._browser: Any = None
        self._page: Any = None

    async def initialize(self) -> None:
        if not get_settings().mcp.browser_enabled:
            logger.info("browser_server_disabled")
            return
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            self._page = await self._browser.new_page()
            logger.info("browser_initialized")
        except Exception as e:
            logger.error("browser_init_failed", error=str(e))

    async def shutdown(self) -> None:
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw") and self._pw:
            await self._pw.stop()
        await super().shutdown()

    def list_tools(self) -> list[Tool]:
        if not get_settings().mcp.browser_enabled:
            return []
        return [
            Tool(name="navigate", server_name=self.server_name,
                 description="Navigate to a URL.",
                 parameters=[ToolParameter("url", "string", "URL to navigate to")]),
            Tool(name="click", server_name=self.server_name,
                 description="Click an element by CSS selector.",
                 parameters=[ToolParameter("selector", "string", "CSS selector")]),
            Tool(name="type_text", server_name=self.server_name,
                 description="Type text into an input field.",
                 parameters=[
                     ToolParameter("selector", "string", "CSS selector of input"),
                     ToolParameter("text", "string", "Text to type"),
                 ]),
            Tool(name="screenshot", server_name=self.server_name,
                 description="Take a screenshot of the current page.",
                 parameters=[]),
            Tool(name="get_page_content", server_name=self.server_name,
                 description="Get the text content of the current page.",
                 parameters=[]),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self._page:
            return ToolResult(content="", is_error=True, error_message="Browser not initialized")
        match tool_name:
            case "navigate":
                await self._page.goto(arguments["url"], wait_until="domcontentloaded")
                return ToolResult(content=f"Navigated to {arguments['url']}", metadata={"url": arguments["url"]})
            case "click":
                await self._page.click(arguments["selector"])
                return ToolResult(content=f"Clicked: {arguments['selector']}")
            case "type_text":
                await self._page.fill(arguments["selector"], arguments["text"])
                return ToolResult(content=f"Typed into: {arguments['selector']}")
            case "screenshot":
                screenshot_bytes = await self._page.screenshot(full_page=False)
                b64 = base64.b64encode(screenshot_bytes).decode()
                return ToolResult(
                    content="Screenshot captured.",
                    artifacts=[{"type": "image", "format": "png", "base64": b64}],
                )
            case "get_page_content":
                content = await self._page.inner_text("body")
                return ToolResult(content=content[:10000])
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown: {tool_name}")
