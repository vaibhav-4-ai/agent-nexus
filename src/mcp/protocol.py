"""
MCP Protocol types — JSON-RPC 2.0 message definitions and tool abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolParameter:
    """A single parameter for a tool."""
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


@dataclass
class Tool:
    """An MCP tool definition."""
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    server_name: str = ""

    def to_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format for LLM function calling."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.parameters:
            properties[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                properties[param.name]["default"] = param.default
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    def to_display(self) -> str:
        """Human-readable tool description for LLM prompts."""
        params_str = ", ".join(
            f"{p.name}: {p.type}" + (f" = {p.default}" if p.default else "")
            for p in self.parameters
        )
        return f"  - {self.name}({params_str}): {self.description}"


@dataclass
class ToolResult:
    """Result from executing an MCP tool."""
    content: str
    is_error: bool = False
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "is_error": self.is_error,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "artifacts": self.artifacts,
        }
