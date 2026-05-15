"""
CAG Manager — Context-Augmented Generation via sliding window.

Maintains a token-limited context window of the most recent actions,
results, and plan state. Included in every LLM call for fast access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import tiktoken

from src.infra.logging import get_logger

logger = get_logger("memory.cag")


@dataclass
class ContextEntry:
    """A single entry in the context window."""
    role: str  # "action", "result", "plan", "system"
    content: str
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class CAGManager:
    """
    Context-Augmented Generation manager.

    Maintains a sliding window of recent context that fits within the
    LLM's context window. This is always included in every LLM call
    for fast, no-retrieval access to recent state.
    """

    def __init__(self, max_tokens: int = 8000) -> None:
        self._entries: list[ContextEntry] = []
        self._max_tokens = max_tokens
        self._current_tokens = 0
        try:
            self._encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoder = None

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self._encoder:
            return len(self._encoder.encode(text))
        return len(text) // 4  # Rough estimate

    def update(self, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Add an entry to the context window."""
        tokens = self._count_tokens(content)
        entry = ContextEntry(role=role, content=content, token_count=tokens, metadata=metadata or {})
        self._entries.append(entry)
        self._current_tokens += tokens
        self._trim_to_fit()

    def _trim_to_fit(self) -> None:
        """FIFO eviction: drop oldest entries until within token limit."""
        while self._current_tokens > self._max_tokens and len(self._entries) > 1:
            removed = self._entries.pop(0)
            self._current_tokens -= removed.token_count
            logger.debug("cag_entry_evicted", role=removed.role, tokens=removed.token_count)

    def get_context(self) -> str:
        """Get the full context window as a formatted string."""
        if not self._entries:
            return "No previous context."
        parts = []
        for entry in self._entries:
            parts.append(f"[{entry.role.upper()}]: {entry.content}")
        return "\n\n".join(parts)

    def get_messages(self) -> list[dict[str, str]]:
        """Get context as a list of messages for LLM API calls."""
        messages = []
        for entry in self._entries:
            role = "assistant" if entry.role in ("result", "plan") else "user"
            messages.append({"role": role, "content": f"[{entry.role}]: {entry.content}"})
        return messages

    @property
    def token_count(self) -> int:
        return self._current_tokens

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        """Clear all context."""
        self._entries.clear()
        self._current_tokens = 0
