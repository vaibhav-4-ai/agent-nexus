"""
Episodic Memory — stores and retrieves past task executions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.infra.logging import get_logger
from src.infra.vector_store import get_vector_store

logger = get_logger("memory.episodic")

COLLECTION_NAME = "episodic_memory"


class EpisodicMemory:
    """
    Stores complete past task executions for experience-based learning.

    When the agent faces a new task, it searches episodic memory for similar
    past tasks to inform planning and avoid repeating mistakes.
    """

    async def store_execution(self, task_record: dict[str, Any]) -> str:
        """Store a completed task execution."""
        store = await get_vector_store()
        record_id = task_record.get("id", str(uuid.uuid4()))

        # Create a searchable summary from the task
        summary_parts = [
            f"Goal: {task_record.get('goal', '')}",
            f"Outcome: {task_record.get('status', 'unknown')}",
            f"Steps taken: {task_record.get('steps_count', 0)}",
        ]
        if task_record.get("what_worked"):
            summary_parts.append(f"What worked: {', '.join(task_record['what_worked'])}")
        if task_record.get("what_failed"):
            summary_parts.append(f"What failed: {', '.join(task_record['what_failed'])}")

        summary_text = "\n".join(summary_parts)

        metadata = {
            "task_id": record_id,
            "goal": task_record.get("goal", "")[:500],
            "status": task_record.get("status", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "steps_count": task_record.get("steps_count", 0),
            "full_record": json.dumps(task_record, default=str)[:2000],
        }

        await store.upsert(COLLECTION_NAME, [summary_text], [metadata], [record_id])
        logger.info("episodic_memory_stored", task_id=record_id, status=metadata["status"])
        return record_id

    async def find_similar(self, goal: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Find similar past task executions."""
        store = await get_vector_store()
        results = await store.search(COLLECTION_NAME, goal, top_k=top_k, score_threshold=0.4)

        experiences = []
        for r in results:
            meta = r.get("metadata", {})
            try:
                full_record = json.loads(meta.get("full_record", "{}"))
            except json.JSONDecodeError:
                full_record = {}
            experiences.append({
                "goal": meta.get("goal", ""),
                "status": meta.get("status", ""),
                "similarity": r.get("score", 0),
                "record": full_record,
                "text": r.get("text", ""),
            })

        logger.debug("episodic_search", goal=goal[:50], results=len(experiences))
        return experiences

    async def get_context(self, goal: str) -> str:
        """Get formatted episodic context for planning."""
        experiences = await self.find_similar(goal)
        if not experiences:
            return "No similar past tasks found."
        parts = []
        for exp in experiences:
            parts.append(
                f"Past task (similarity: {exp['similarity']:.2f}):\n"
                f"  Goal: {exp['goal']}\n"
                f"  Status: {exp['status']}\n"
                f"  Details: {exp['text'][:300]}"
            )
        return "Similar past experiences:\n\n" + "\n\n".join(parts)
