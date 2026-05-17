"""
HTTP MCP Server — make HTTP requests and fetch web pages.

Security model (S4 fix):
- SSRF protection: every URL is validated by `_is_safe_url()` before dispatch.
- Blocks: cloud metadata endpoints (169.254.169.254, metadata.google.internal,
  metadata.azure.com), loopback (127.x), RFC1918 private (10/8, 172.16/12,
  192.168/16), link-local (169.254/16), multicast, reserved.
- Allowed schemes: http, https only.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from src.infra.logging import get_logger
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.http")

# Hostnames that are always rejected regardless of DNS resolution
BLOCKED_HOSTNAMES = frozenset({
    "metadata", "metadata.google.internal", "metadata.azure.com",
    "instance-data", "instance-data.ec2.internal",
    "localhost",
})
# Hostname suffixes that are always rejected
BLOCKED_HOSTNAME_SUFFIXES = (".local", ".internal", ".cluster.local")


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Returns (safe, reason). reason is a short explanation on rejection."""
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable url: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme '{parsed.scheme}' not allowed (only http/https)"
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "empty hostname"
    if hostname in BLOCKED_HOSTNAMES:
        return False, f"hostname '{hostname}' is in deny list (cloud metadata / local)"
    for suffix in BLOCKED_HOSTNAME_SUFFIXES:
        if hostname.endswith(suffix):
            return False, f"hostname suffix '{suffix}' blocked"
    # IP literal check
    try:
        ip = ipaddress.ip_address(hostname)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False, f"IP {ip} is private/loopback/link-local/multicast/reserved"
        return True, ""
    except ValueError:
        pass  # Not a literal IP — must resolve via DNS
    # DNS resolution check (catches hostnames that resolve to private IPs)
    try:
        # Only resolve A/AAAA records; skip if DNS is slow/unavailable
        # (in that case the request will fail naturally on dispatch).
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if (ip.is_private or ip.is_loopback or ip.is_link_local
                        or ip.is_multicast or ip.is_reserved):
                    return False, f"host '{hostname}' resolves to private/loopback IP {ip}"
            except ValueError:
                continue
    except (socket.gaierror, OSError):
        pass  # DNS failed — let the actual request fail
    return True, ""


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

        safe, reason = _is_safe_url(url)
        if not safe:
            logger.warning("http_ssrf_blocked", url=url, reason=reason)
            return ToolResult(content="", is_error=True,
                              error_message=f"URL rejected by SSRF filter: {reason}")

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
        safe, reason = _is_safe_url(url)
        if not safe:
            logger.warning("http_ssrf_blocked", url=url, reason=reason)
            return ToolResult(content="", is_error=True,
                              error_message=f"URL rejected by SSRF filter: {reason}")
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
