"""
Shell MCP Server — sandboxed command execution.

Executes shell commands with timeout, blocklist, and output capture.

Security model (S3 fix):
- Defense in depth: substring blocklist (literal danger), metacharacter rejector
  (rejects `$(...)`, backticks, `&&`, `||`, `;`, `|`, `>`, `<`, `&`), and
  exec-style subprocess (`asyncio.create_subprocess_exec`) instead of shell
  interpretation. The agent gets a single command argv each call — no shell
  features. For anything needing shell features, the agent should switch to
  the code_executor MCP server, which has its own sandboxing.
- Original blocklist retained as third layer.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.shell")


# Commands that are never allowed (literal substring match)
BLOCKED_COMMANDS = {"rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:", "shutdown", "reboot", "halt", "poweroff"}

# Shell metacharacters — presence in command implies user is trying to invoke
# shell features. Reject all of them. Comments-style # is also blocked so a
# user can't smuggle dangerous content past the substring check via a comment.
SHELL_METACHARS = ("`", "$(", "${", "&&", "||", ";", "|", ">", "<", "&", "\n", "\r", "#")


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
        """Check if a command is in the literal blocklist (defense-in-depth layer 1)."""
        cmd_lower = command.lower().strip()
        return any(blocked in cmd_lower for blocked in BLOCKED_COMMANDS)

    def _has_shell_metachars(self, command: str) -> tuple[bool, str]:
        """Check for shell metacharacters (defense-in-depth layer 2).

        Returns (found, char) where char is the first offending sequence.
        """
        for token in SHELL_METACHARS:
            if token in command:
                return True, token
        return False, ""

    async def _execute_command(self, command: str, timeout: int) -> ToolResult:
        # Layer 1: literal blocklist
        if self._is_blocked(command):
            logger.warning("shell_command_blocked_literal", command=command[:120])
            return ToolResult(content="", is_error=True,
                              error_message=f"Command blocked for safety: {command}")

        # Layer 2: metacharacter rejector. Agent must use code_executor for
        # anything that needs pipes/redirection/substitution/multiple commands.
        has_meta, offending = self._has_shell_metachars(command)
        if has_meta:
            logger.warning("shell_command_blocked_metachars",
                           command=command[:120], offending=offending)
            return ToolResult(
                content="", is_error=True,
                error_message=(f"Shell metacharacters not allowed (found: '{offending}'). "
                               f"Use the code_executor server for complex flows."),
            )

        # Layer 3: parse argv with shlex, dispatch via exec (no shell interp).
        try:
            argv = shlex.split(command)
        except ValueError as e:
            return ToolResult(content="", is_error=True,
                              error_message=f"Could not parse command: {e}")
        if not argv:
            return ToolResult(content="", is_error=True, error_message="Empty command")

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
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
        # Scripts intentionally allow shell features (loops, pipes), but we still
        # apply the literal danger blocklist to the script body. Per-line metachar
        # blocking would defeat scripting's purpose — but `rm -rf /` etc. are
        # never legitimate.
        if self._is_blocked(script):
            logger.warning("shell_script_blocked", script_preview=script[:120])
            return ToolResult(content="", is_error=True,
                              error_message="Script contains a blocked danger pattern")

        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, dir=self._workspace) as f:
            f.write("#!/bin/bash\nset -e\n" + script)
            script_path = f.name

        try:
            os.chmod(script_path, 0o755)
            # Call subprocess_exec directly here — bypassing _execute_command's
            # metacharacter rejector, since we WANT bash to interpret the script.
            try:
                process = await asyncio.create_subprocess_exec(
                    "bash", script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self._workspace,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                stdout_str = stdout.decode("utf-8", errors="replace").strip()
                stderr_str = stderr.decode("utf-8", errors="replace").strip()
                exit_code = process.returncode or 0
                parts = []
                if stdout_str:
                    parts.append(f"STDOUT:\n{stdout_str}")
                if stderr_str:
                    parts.append(f"STDERR:\n{stderr_str}")
                parts.append(f"EXIT CODE: {exit_code}")
                return ToolResult(
                    content="\n\n".join(parts),
                    is_error=exit_code != 0,
                    error_message=stderr_str if exit_code != 0 else "",
                    metadata={"exit_code": exit_code, "script_path": script_path},
                )
            except asyncio.TimeoutError:
                return ToolResult(content="", is_error=True,
                                  error_message=f"Script timed out after {timeout}s")
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass
