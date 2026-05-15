"""
Graph Memory — lightweight knowledge graph for entities and relations.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger

logger = get_logger("memory.graph")


class GraphMemory:
    """
    In-memory knowledge graph with Postgres persistence.

    Tracks entities (files, APIs, errors) and relations discovered during tasks.
    """

    def __init__(self) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        self._relations: list[tuple[str, str, str]] = []  # (entity1, relation, entity2)

    async def add_entity(self, name: str, entity_type: str,
                          properties: dict[str, Any] | None = None) -> None:
        """Add or update an entity."""
        self._entities[name] = {
            "type": entity_type,
            "properties": properties or {},
        }
        logger.debug("entity_added", name=name, type=entity_type)

    async def add_relation(self, entity1: str, relation: str, entity2: str) -> None:
        """Add a directed relation between two entities."""
        self._relations.append((entity1, relation, entity2))
        # Auto-create entities if they don't exist
        if entity1 not in self._entities:
            self._entities[entity1] = {"type": "unknown", "properties": {}}
        if entity2 not in self._entities:
            self._entities[entity2] = {"type": "unknown", "properties": {}}

    async def query(self, entity: str) -> dict[str, Any]:
        """Get all information about an entity and its relations."""
        info = self._entities.get(entity, {})
        outgoing = [(r, e2) for e1, r, e2 in self._relations if e1 == entity]
        incoming = [(e1, r) for e1, r, e2 in self._relations if e2 == entity]
        return {
            "entity": entity,
            "info": info,
            "outgoing_relations": [{"relation": r, "target": e} for r, e in outgoing],
            "incoming_relations": [{"source": e, "relation": r} for e, r in incoming],
        }

    async def get_context(self) -> str:
        """Get a summary of the knowledge graph for LLM context."""
        if not self._entities:
            return "Knowledge graph is empty."
        parts = [f"Known entities ({len(self._entities)}):"]
        for name, info in list(self._entities.items())[:20]:
            parts.append(f"  - {name} ({info['type']})")
        if self._relations:
            parts.append(f"\nRelations ({len(self._relations)}):")
            for e1, r, e2 in self._relations[:20]:
                parts.append(f"  - {e1} --[{r}]--> {e2}")
        return "\n".join(parts)

    def clear(self) -> None:
        self._entities.clear()
        self._relations.clear()
