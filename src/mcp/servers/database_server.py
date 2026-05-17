"""
Database MCP Server — execute PostgreSQL queries.
"""

from __future__ import annotations

from typing import Any

from src.infra.db import _get_engine
from src.infra.logging import get_logger
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.servers.base_server import BaseMCPServer

logger = get_logger("mcp.database")


class DatabaseServer(BaseMCPServer):
    """MCP server for PostgreSQL query execution."""

    @property
    def server_name(self) -> str:
        return "database"

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="execute_query", server_name=self.server_name,
                description="Execute a read-only SQL SELECT query and return results. "
                            "Mutations (INSERT/UPDATE/DELETE/DDL) are not permitted.",
                parameters=[
                    ToolParameter("query", "string", "SQL SELECT query to execute"),
                ],
            ),
            Tool(
                name="list_tables", server_name=self.server_name,
                description="List all tables in the database.",
                parameters=[],
            ),
            Tool(
                name="describe_table", server_name=self.server_name,
                description="Get the schema of a table.",
                parameters=[ToolParameter("table_name", "string", "Name of the table")],
            ),
        ]

    async def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        match tool_name:
            case "execute_query":
                # Ignore any read_only arg the LLM may pass — we don't trust it.
                return await self._execute_query(arguments["query"])
            case "list_tables":
                return await self._list_tables()
            case "describe_table":
                return await self._describe_table(arguments["table_name"])
            case _:
                return ToolResult(content="", is_error=True, error_message=f"Unknown tool: {tool_name}")

    async def _execute_query(self, query: str) -> ToolResult:
        """Execute a SELECT-only query.

        S5: The previous `read_only` flag was set by the LLM, which we don't
        trust. Now the rule is hard-coded: a query is permitted iff its first
        non-whitespace token is SELECT or WITH (for CTE pattern `WITH x AS
        (SELECT ...) SELECT ...`). All mutations and DDL are rejected.
        """
        stripped = query.strip()
        if not stripped:
            return ToolResult(content="", is_error=True, error_message="Empty query")
        # Strip leading SQL comments
        import re
        cleaned = re.sub(r"^\s*(?:--[^\n]*\n|/\*.*?\*/)\s*", "", stripped, flags=re.DOTALL)
        first_token = cleaned.lstrip("(").split(None, 1)[0].upper() if cleaned else ""
        if first_token not in ("SELECT", "WITH"):
            logger.warning("db_query_blocked", first_token=first_token,
                           preview=stripped[:120])
            return ToolResult(content="", is_error=True,
                              error_message=(f"Only SELECT or WITH (CTE) queries are permitted. "
                                             f"Got: {first_token or '<empty>'}"))

        engine = _get_engine()
        try:
            from sqlalchemy import text
            async with engine.connect() as conn:
                result = await conn.execute(text(query))
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = result.fetchall()
                    # Format as table
                    header = " | ".join(columns)
                    separator = "-|-".join("-" * len(c) for c in columns)
                    data_rows = [" | ".join(str(v) for v in row) for row in rows[:100]]
                    table = f"{header}\n{separator}\n" + "\n".join(data_rows)
                    return ToolResult(
                        content=f"Query returned {len(rows)} rows:\n\n{table}",
                        metadata={"row_count": len(rows), "columns": columns},
                    )
                else:
                    await conn.commit()
                    return ToolResult(content=f"Query executed successfully. Rows affected: {result.rowcount}")
        except Exception as e:
            return ToolResult(content="", is_error=True, error_message=f"Query failed: {e}")

    async def _list_tables(self) -> ToolResult:
        query = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        return await self._execute_query(query)

    async def _describe_table(self, table_name: str) -> ToolResult:
        # Sanitize table name
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            return ToolResult(content="", is_error=True, error_message="Invalid table name")
        query = (
            f"SELECT column_name, data_type, is_nullable, column_default "
            f"FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position"
        )
        return await self._execute_query(query)
