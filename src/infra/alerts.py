"""
Quota Manager — auto-remediates free-tier quota pressure.

Designed for zero human intervention: when Qdrant or `knowledge_graph` storage
approaches its free-tier limit, the manager evicts the oldest 30% of data
(LRU-style) to bring usage back under control. If an optional webhook is
configured, an informational notification is sent (Discord/Slack/ntfy.sh).
The eviction happens whether or not the webhook is reachable.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import delete, func, select

from src.config import get_settings
from src.infra.logging import get_logger

logger = get_logger("infra.alerts")


class QuotaManager:
    """Periodic checker + auto-evictor for free-tier quotas."""

    EPISODIC_COLLECTION = "episodic_memory"

    def __init__(self) -> None:
        self._settings = get_settings()

    async def check_and_remediate(self) -> dict[str, Any]:
        """Run all checks. Returns a dict summarizing any actions taken."""
        actions: dict[str, Any] = {}
        try:
            qd = await self._check_qdrant()
            if qd:
                actions["qdrant_episodic"] = qd
        except Exception as e:
            logger.warning("qdrant_quota_check_failed", error=str(e))
        try:
            kg = await self._check_knowledge_graph()
            if kg:
                actions["knowledge_graph"] = kg
        except Exception as e:
            logger.warning("kg_quota_check_failed", error=str(e))
        try:
            fr = await self._check_failure_rate()
            if fr:
                actions["failure_rate"] = fr
        except Exception as e:
            logger.warning("failure_rate_check_failed", error=str(e))

        if actions:
            logger.info("quota_manager_remediation", actions=actions)
        return actions

    # --- Qdrant episodic vectors ---
    async def _check_qdrant(self) -> dict[str, Any] | None:
        from src.infra.vector_store import get_vector_store

        store = await get_vector_store()
        try:
            before_bytes = await store.estimate_size_bytes(self.EPISODIC_COLLECTION)
        except Exception as e:
            logger.warning("qdrant_size_estimate_failed", error=str(e))
            return None

        threshold = self._settings.monitoring.quota_qdrant_threshold_bytes
        if before_bytes < threshold:
            return None

        count = await store.count(self.EPISODIC_COLLECTION)
        to_delete = max(1, int(count * 0.30))
        deleted = await store.delete_oldest(self.EPISODIC_COLLECTION, to_delete)
        after_bytes = await store.estimate_size_bytes(self.EPISODIC_COLLECTION)

        payload = {
            "level": "info",
            "event": "auto_eviction_completed",
            "resource": "qdrant_episodic",
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "evicted_count": deleted,
            "message": f"Auto-evicted {deleted} oldest episodic memory vectors "
                       f"(was {before_bytes:,}B, now {after_bytes:,}B; threshold {threshold:,}B).",
        }
        await self._notify(payload)
        return payload

    # --- knowledge_graph table ---
    async def _check_knowledge_graph(self) -> dict[str, Any] | None:
        from src.infra.db import _get_session_factory
        from src.infra.db import KnowledgeGraphModel

        factory = _get_session_factory()
        threshold = self._settings.monitoring.quota_kg_row_threshold

        async with factory() as session:
            total = await session.scalar(select(func.count()).select_from(KnowledgeGraphModel))
            total = int(total or 0)
            if total < threshold:
                return None
            to_delete = max(1, int(total * 0.30))
            # Find the cutoff timestamp: the (to_delete)-th oldest row
            subq = (
                select(KnowledgeGraphModel.id)
                .order_by(KnowledgeGraphModel.created_at.asc())
                .limit(to_delete)
            )
            ids_result = await session.execute(subq)
            ids = [row[0] for row in ids_result.all()]
            if not ids:
                return None
            await session.execute(
                delete(KnowledgeGraphModel).where(KnowledgeGraphModel.id.in_(ids))
            )
            await session.commit()
            remaining = total - len(ids)

        payload = {
            "level": "info",
            "event": "auto_eviction_completed",
            "resource": "knowledge_graph",
            "before_rows": total,
            "after_rows": remaining,
            "evicted_count": len(ids),
            "message": f"Auto-evicted {len(ids)} oldest knowledge_graph rows "
                       f"(was {total}, now {remaining}; threshold {threshold}).",
        }
        await self._notify(payload)
        return payload

    # --- Task failure rate (informational only) ---
    async def _check_failure_rate(self) -> dict[str, Any] | None:
        from src.infra.metrics import get_metrics

        metrics = await get_metrics()
        summary = metrics.get_summary() or {}
        total = summary.get("total_tasks") or summary.get("tasks_total") or 0
        failed = summary.get("tasks_failed") or 0
        if total < 20:  # not enough data
            return None
        rate = failed / total if total > 0 else 0
        if rate < 0.5:
            return None

        payload = {
            "level": "warning",
            "event": "high_failure_rate",
            "resource": "tasks",
            "total": total,
            "failed": failed,
            "rate": round(rate, 3),
            "message": f"Task failure rate is {rate:.0%} ({failed}/{total}). "
                       f"No auto-action taken — this is informational only.",
        }
        await self._notify(payload)
        return payload

    # --- Webhook notification (optional) ---
    async def _notify(self, payload: dict[str, Any]) -> None:
        url = self._settings.monitoring.alert_webhook_url
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Heuristic: Slack/Discord/ntfy.sh all accept JSON POST.
                # For ntfy.sh, also send a plain-text body and title header.
                if "ntfy.sh" in url:
                    await client.post(
                        url,
                        content=payload.get("message", "Agent Nexus event").encode(),
                        headers={"Title": payload.get("event", "agent-nexus")},
                    )
                else:
                    await client.post(url, json=payload)
        except Exception as e:
            logger.warning("alert_webhook_failed", url=url, error=str(e))


# Module-level singleton (optional convenience)
_quota_manager: QuotaManager | None = None


def get_quota_manager() -> QuotaManager:
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
