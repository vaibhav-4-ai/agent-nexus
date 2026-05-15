# Adding Custom MCP Servers

## Overview
MCP (Model Context Protocol) servers are how the agent interacts with the world. Each server provides a set of tools that the agent can invoke during task execution.

## Steps to Add a New Server

### 1. Create Your Server File
Create a new file in `src/mcp/servers/`:

```python
from src.mcp.servers.base_server import BaseMCPServer
from src.mcp.protocol import Tool, ToolParameter, ToolResult

class MyCustomServer(BaseMCPServer):
    @property
    def server_name(self) -> str:
        return "my_custom"

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="my_tool",
                server_name=self.server_name,
                description="Does something useful",
                parameters=[
                    ToolParameter("input", "string", "The input data"),
                ],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict) -> ToolResult:
        if tool_name == "my_tool":
            result = f"Processed: {arguments['input']}"
            return ToolResult(content=result)
        return ToolResult(content="", is_error=True, error_message="Unknown tool")
```

### 2. Register in MCPClient
Add your server to `src/mcp/client.py` in `initialize_all_servers()`:

```python
from src.mcp.servers.my_custom_server import MyCustomServer
servers = [..., MyCustomServer()]
```

### 3. That's it!
The agent will automatically discover your tools and use them when appropriate.
