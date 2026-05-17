"""
Code Execution MCP Server — run Python/JS in isolated subprocesses.

Security model (S7 fix):
- Subprocess receives a CURATED env, not the full parent environment.
- Removes all API keys / DB URLs / tokens / secrets from view of user code.
- Preserves only PATH / LANG / locale / standard variables so common imports
  (requests, json, etc.) still work.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from src.config import get_settings
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer


# Whitelist of env var names that user code is allowed to see.
# Any var NOT in this set is stripped (api keys, db urls, secrets, etc.).
SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "NODE_OPTIONS",
    "TMPDIR", "TMP", "TEMP",
})


def _build_safe_env() -> dict[str, str]:
    """Return a dict containing only the env vars in SAFE_ENV_KEYS."""
    return {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}


class CodeExecServer(BaseMCPServer):
    """MCP server for sandboxed code execution."""

    @property
    def server_name(self) -> str:
        return "code_executor"

    def __init__(self) -> None:
        self._workspace = get_settings().mcp.workspace_dir
        self._timeout = get_settings().mcp.shell_timeout

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="execute_python", server_name=self.server_name,
                description="Execute Python code and return the output.",
                parameters=[
                    ToolParameter("code", "string", "Python code to execute"),
                    ToolParameter("timeout", "integer", "Timeout in seconds", required=False, default=30),
                ],
            ),
            Tool(
                name="execute_javascript", server_name=self.server_name,
                description="Execute JavaScript code via Node.js.",
                parameters=[
                    ToolParameter("code", "string", "JavaScript code to execute"),
                    ToolParameter("timeout", "integer", "Timeout in seconds", required=False, default=30),
                ],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        timeout = arguments.get("timeout", self._timeout)
        match tool_name:
            case "execute_python":
                return await self._run_code(arguments["code"], "python3", ".py", timeout)
            case "execute_javascript":
                return await self._run_code(arguments["code"], "node", ".js", timeout)
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown: {tool_name}")

    async def _run_code(self, code: str, interpreter: str, ext: str, timeout: int) -> ToolResult:
        # Write code to temp file
        fd, path = tempfile.mkstemp(suffix=ext, dir=self._workspace)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)

            process = await asyncio.create_subprocess_exec(
                interpreter, path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace,
                env=_build_safe_env(),  # S7: strip all secrets from view of user code
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                return ToolResult(content="", is_error=True, error_message=f"Execution timed out after {timeout}s")

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            parts = []
            if stdout_str:
                parts.append(f"OUTPUT:\n{stdout_str}")
            if stderr_str:
                parts.append(f"ERRORS:\n{stderr_str}")
            parts.append(f"EXIT CODE: {exit_code}")

            return ToolResult(
                content="\n\n".join(parts),
                is_error=exit_code != 0,
                error_message=stderr_str if exit_code != 0 else "",
                metadata={"exit_code": exit_code, "interpreter": interpreter},
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
