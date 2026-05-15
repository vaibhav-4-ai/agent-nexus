"""
Memory Router — decides which memory sources to query.

Chain of Responsibility: routes queries through CAG → RAG → Episodic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.infra.logging import get_logger
from src.infra.metrics import get_metrics
from src.memory.cag_manager import CAGManager
from src.memory.episodic import EpisodicMemory
from src.memory.graph_memory import GraphMemory
from src.memory.rag_engine import RAGEngine

logger = get_logger("memory.router")


@dataclass
class MemoryContext:
    """Combined context from all memory sources."""
    cag_context: str = ""
    rag_context: str = ""
    episodic_context: str = ""
    graph_context: str = ""

    def to_prompt(self) -> str:
        """Format all context for an LLM prompt."""
        parts = []
        if self.cag_context and self.cag_context != "No previous context.":
            parts.append(f"## Recent Context\n{self.cag_context}")
        if self.rag_context and self.rag_context != "No relevant documents found.":
            parts.append(f"## Relevant Knowledge\n{self.rag_context}")
        if self.episodic_context and self.episodic_context != "No similar past tasks found.":
            parts.append(f"## Past Experience\n{self.episodic_context}")
        if self.graph_context and self.graph_context != "Knowledge graph is empty.":
            parts.append(f"## Known Entities\n{self.graph_context}")
        return "\n\n".join(parts) if parts else "No relevant context available."


class MemoryRouter:
    """
    Intelligent memory routing — decides which sources to query.

    Rules:
    1. CAG is ALWAYS included (zero cost, already in memory).
    2. RAG is added when the step requires domain knowledge.
    3. Episodic is added when the goal resembles a past task.
    4. Graph is added when entities are referenced.
    """

    def __init__(
        self,
        cag: CAGManager,
        rag: RAGEngine,
        episodic: EpisodicMemory,
        graph: GraphMemory,
    ) -> None:
        self._cag = cag
        self._rag = rag
        self._episodic = episodic
        self._graph = graph

    async def get_context(self, step_description: str, goal: str = "",
                           include_rag: bool = True, include_episodic: bool = True) -> MemoryContext:
        """Get context from all relevant memory sources."""
        metrics = await get_metrics()
        context = MemoryContext()

        # 1. CAG — always included
        context.cag_context = self._cag.get_context()
        await metrics.increment("agent_memory_queries", labels={"type": "cag"})

        # 2. RAG — if the step seems knowledge-seeking
        if include_rag and self._needs_rag(step_description):
            try:
                context.rag_context = await self._rag.get_context(step_description)
                await metrics.increment("agent_memory_queries", labels={"type": "rag"})
            except Exception as e:
                logger.warning("rag_query_failed", error=str(e))
                context.rag_context = ""

        # 3. Episodic — if goal provided and might match past tasks
        if include_episodic and goal:
            try:
                context.episodic_context = await self._episodic.get_context(goal)
                await metrics.increment("agent_memory_queries", labels={"type": "episodic"})
            except Exception as e:
                logger.warning("episodic_query_failed", error=str(e))
                context.episodic_context = ""

        # 4. Graph — always include a brief summary
        try:
            context.graph_context = await self._graph.get_context()
        except Exception as e:
            logger.warning("graph_query_failed", error=str(e))

        return context

    @staticmethod
    def _needs_rag(step_description: str) -> bool:
        """Heuristic: does this step need RAG retrieval?"""
        knowledge_keywords = {
            "find", "search", "look up", "what is", "how to", "documentation",
            "explain", "describe", "information", "reference", "guide",
            "api", "library", "function", "method", "class",
        }
        desc_lower = step_description.lower()
        return any(kw in desc_lower for kw in knowledge_keywords)
