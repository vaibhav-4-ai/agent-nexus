"""
Filesystem MCP Server — sandboxed file operations.

Provides tools for reading, writing, listing, and searching files
within a configurable workspace directory. Path traversal is prevented.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiofiles

from src.config import get_settings
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer


class FilesystemServer(BaseMCPServer):
    """MCP server for sandboxed filesystem operations."""

    @property
    def server_name(self) -> str:
        return "filesystem"

    def __init__(self) -> None:
        settings = get_settings()
        self._workspace = Path(settings.mcp.workspace_dir)

    async def initialize(self) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        await super().initialize()

    def _resolve_safe_path(self, path: str) -> Path:
        """Resolve a path within the sandbox, preventing traversal."""
        resolved = (self._workspace / path).resolve()
        if not str(resolved).startswith(str(self._workspace.resolve())):
            raise PermissionError(f"Path '{path}' escapes sandbox")
        return resolved

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="read_file", server_name=self.server_name,
                description="Read the contents of a file.",
                parameters=[ToolParameter("path", "string", "Relative path to the file")],
            ),
            Tool(
                name="write_file", server_name=self.server_name,
                description="Write content to a file (creates parent dirs).",
                parameters=[
                    ToolParameter("path", "string", "Relative path to the file"),
                    ToolParameter("content", "string", "Content to write"),
                ],
            ),
            Tool(
                name="list_directory", server_name=self.server_name,
                description="List files and directories at the given path.",
                parameters=[
                    ToolParameter("path", "string", "Relative directory path", default="."),
                ],
            ),
            Tool(
                name="search_files", server_name=self.server_name,
                description="Search for files matching a pattern.",
                parameters=[
                    ToolParameter("pattern", "string", "Glob pattern (e.g., '*.py')"),
                    ToolParameter("path", "string", "Directory to search", required=False, default="."),
                ],
            ),
            Tool(
                name="get_file_info", server_name=self.server_name,
                description="Get metadata about a file (size, modified time).",
                parameters=[ToolParameter("path", "string", "Relative path to the file")],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "read_file":
                return await self._read_file(arguments["path"])
            case "write_file":
                return await self._write_file(arguments["path"], arguments["content"])
            case "list_directory":
                return await self._list_directory(arguments.get("path", "."))
            case "search_files":
                return await self._search_files(arguments["pattern"], arguments.get("path", "."))
            case "get_file_info":
                return await self._get_file_info(arguments["path"])
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown tool: {tool_name}")

    async def _read_file(self, path: str) -> ToolResult:
        resolved = self._resolve_safe_path(path)
        if not resolved.exists():
            return ToolResult(content="", is_error=True, error_message=f"File not found: {path}")
        if not resolved.is_file():
            return ToolResult(content="", is_error=True, error_message=f"Not a file: {path}")
        async with aiofiles.open(resolved, "r") as f:
            content = await f.read()
        return ToolResult(
            content=content,
            metadata={"path": str(resolved), "size_bytes": resolved.stat().st_size},
        )

    async def _write_file(self, path: str, content: str) -> ToolResult:
        resolved = self._resolve_safe_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(resolved, "w") as f:
            await f.write(content)
        return ToolResult(
            content=f"File written successfully: {path} ({len(content)} bytes)",
            metadata={"path": str(resolved), "size_bytes": len(content)},
        )

    async def _list_directory(self, path: str) -> ToolResult:
        resolved = self._resolve_safe_path(path)
        if not resolved.exists() or not resolved.is_dir():
            return ToolResult(content="", is_error=True, error_message=f"Directory not found: {path}")
        entries = []
        for item in sorted(resolved.iterdir()):
            entry_type = "dir" if item.is_dir() else "file"
            size = item.stat().st_size if item.is_file() else 0
            entries.append(f"  [{entry_type}] {item.name}" + (f" ({size} bytes)" if size else ""))
        return ToolResult(content=f"Contents of {path}:\n" + "\n".join(entries))

    async def _search_files(self, pattern: str, path: str) -> ToolResult:
        resolved = self._resolve_safe_path(path)
        if not resolved.is_dir():
            return ToolResult(content="", is_error=True, error_message=f"Not a directory: {path}")
        matches = list(resolved.rglob(pattern))[:50]  # Limit results
        if not matches:
            return ToolResult(content=f"No files matching '{pattern}' found in {path}")
        rel_paths = [str(m.relative_to(self._workspace)) for m in matches]
        return ToolResult(content=f"Found {len(matches)} files:\n" + "\n".join(f"  {p}" for p in rel_paths))

    async def _get_file_info(self, path: str) -> ToolResult:
        resolved = self._resolve_safe_path(path)
        if not resolved.exists():
            return ToolResult(content="", is_error=True, error_message=f"Not found: {path}")
        stat = resolved.stat()
        info = (
            f"Path: {path}\n"
            f"Type: {'directory' if resolved.is_dir() else 'file'}\n"
            f"Size: {stat.st_size} bytes\n"
            f"Modified: {stat.st_mtime}\n"
        )
        return ToolResult(content=info, metadata={"size": stat.st_size})
