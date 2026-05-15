"""
Pytest fixtures for agent-nexus tests.

Provides mock LLM, mock MCP client, and test database fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.llm.provider import LLMProvider, LLMResponse
from src.llm.structured_output import ExecutionPlan, ParsedGoal, PlanStep, VerificationResult
from src.mcp.client import MCPClient
from src.mcp.protocol import Tool, ToolParameter, ToolResult
from src.mcp.registry import ServerRegistry
from src.memory.cag_manager import CAGManager


@pytest.fixture
def parsed_goal() -> ParsedGoal:
    return ParsedGoal(
        objective="Find all Python files and count lines of code",
        constraints=["Only .py files"],
        success_criteria=["List of files with line counts"],
        required_modalities=["code"],
        complexity="low",
        estimated_steps=3,
    )


@pytest.fixture
def execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_summary="List Python files, then count lines in each",
        steps=[
            PlanStep(step_number=1, description="List .py files", tool="search_files",
                     expected_args={"pattern": "*.py"}, expected_outcome="List of Python files"),
            PlanStep(step_number=2, description="Read and count lines", tool="read_file",
                     expected_args={"path": "test.py"}, expected_outcome="File content with line count"),
        ],
    )


@pytest.fixture
def mock_llm_provider() -> LLMProvider:
    """Create a mock LLM provider that returns predictable responses."""
    provider = LLMProvider()
    provider.complete = AsyncMock(return_value=LLMResponse(
        content='{"objective": "test", "constraints": [], "success_criteria": [], '
                '"required_modalities": [], "complexity": "low", "estimated_steps": 1}',
        model="test-model",
        input_tokens=10,
        output_tokens=20,
    ))
    provider.stream = AsyncMock()
    return provider


@pytest.fixture
def mock_tool_result() -> ToolResult:
    return ToolResult(
        content="test.py\nmain.py\nutils.py",
        is_error=False,
        metadata={"tool": "search_files"},
    )


@pytest.fixture
def mock_error_result() -> ToolResult:
    return ToolResult(
        content="",
        is_error=True,
        error_message="File not found: missing.py",
    )


@pytest.fixture
def verification_pass() -> VerificationResult:
    return VerificationResult(verified=True, confidence=0.95, evidence_summary="Action succeeded")


@pytest.fixture
def verification_fail() -> VerificationResult:
    return VerificationResult(verified=False, confidence=0.3, evidence_summary="Action failed")


@pytest.fixture
def cag_manager() -> CAGManager:
    return CAGManager(max_tokens=1000)


@pytest.fixture
def sample_tools() -> list[Tool]:
    return [
        Tool(name="read_file", description="Read a file",
             parameters=[ToolParameter("path", "string", "File path")], server_name="filesystem"),
        Tool(name="execute_command", description="Run a command",
             parameters=[ToolParameter("command", "string", "Shell command")], server_name="shell"),
    ]
