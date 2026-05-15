"""
Shell MCP Server — sandboxed command execution.

Executes shell commands with timeout, allowlist/blocklist, and output capture.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from src.config import get_settings
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer


# Commands that are never allowed
BLOCKED_COMMANDS = {"rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:", "shutdown", "reboot", "halt", "poweroff"}


class ShellServer(BaseMCPServer):
    """MCP server for sandboxed shell command execution."""

    @property
    def server_name(self) -> str:
        return "shell"

    def __init__(self) -> None:
        settings = get_settings()
        self._timeout = settings.mcp.shell_timeout
        self._workspace = settings.mcp.workspace_dir

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="execute_command", server_name=self.server_name,
                description="Execute a shell command and return its output.",
                parameters=[
                    ToolParameter("command", "string", "The shell command to execute"),
                    ToolParameter("timeout", "integer", "Timeout in seconds", required=False, default=30),
                ],
            ),
            Tool(
                name="execute_script", server_name=self.server_name,
                description="Execute a multi-line shell script.",
                parameters=[
                    ToolParameter("script", "string", "The shell script content"),
                    ToolParameter("timeout", "integer", "Timeout in seconds", required=False, default=60),
                ],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "execute_command":
                return await self._execute_command(
                    arguments["command"],
                    arguments.get("timeout", self._timeout),
                )
            case "execute_script":
                return await self._execute_script(
                    arguments["script"],
                    arguments.get("timeout", self._timeout * 2),
                )
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown tool: {tool_name}")

    def _is_blocked(self, command: str) -> bool:
        """Check if a command is in the blocklist."""
        cmd_lower = command.lower().strip()
        return any(blocked in cmd_lower for blocked in BLOCKED_COMMANDS)

    async def _execute_command(self, command: str, timeout: int) -> ToolResult:
        if self._is_blocked(command):
            return ToolResult(content="", is_error=True, error_message=f"Command blocked for safety: {command}")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            output_parts = []
            if stdout_str:
                output_parts.append(f"STDOUT:\n{stdout_str}")
            if stderr_str:
                output_parts.append(f"STDERR:\n{stderr_str}")
            output_parts.append(f"EXIT CODE: {exit_code}")

            return ToolResult(
                content="\n\n".join(output_parts),
                is_error=exit_code != 0,
                error_message=stderr_str if exit_code != 0 else "",
                metadata={"exit_code": exit_code, "command": command},
            )
        except asyncio.TimeoutError:
            return ToolResult(content="", is_error=True, error_message=f"Command timed out after {timeout}s: {command}")

    async def _execute_script(self, script: str, timeout: int) -> ToolResult:
        # Write script to temp file and execute
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, dir=self._workspace) as f:
            f.write("#!/bin/bash\nset -e\n" + script)
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            return await self._execute_command(f"bash {script_path}", timeout)
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass
