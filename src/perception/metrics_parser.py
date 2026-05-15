"""
Metrics Parser — query and analyze agent metrics.
"""

from __future__ import annotations

from typing import Any

from src.infra.logging import get_logger
from src.infra.metrics import get_metrics

logger = get_logger("perception.metrics")


class MetricsParser:
    """Parse and analyze agent performance metrics."""

    async def get_task_metrics(self, time_range_hours: int = 24) -> dict[str, Any]:
        """Get task-level metrics summary."""
        metrics = await get_metrics()
        return metrics.get_summary()

    async def detect_anomalies(self, metric_name: str) -> list[dict[str, Any]]:
        """Simple z-score anomaly detection on metric values."""
        metrics = await get_metrics()
        all_metrics = metrics.get_all_metrics()

        # Find all values for this metric
        values = [v for k, v in all_metrics.items() if k.startswith(metric_name)]
        if len(values) < 3:
            return []

        import statistics
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0

        anomalies = []
        for i, val in enumerate(values):
            if stdev > 0:
                z_score = abs(val - mean) / stdev
                if z_score > 2.0:
                    anomalies.append({"index": i, "value": val, "z_score": z_score, "type": "high" if val > mean else "low"})

        return anomalies

    async def summarize_metrics(self) -> str:
        """Get a human-readable metrics summary."""
        metrics = await get_metrics()
        summary = metrics.get_summary()
        lines = [
            f"Total tasks: {summary['total_tasks']}",
            f"Completed: {summary['tasks_completed']}",
            f"Failed: {summary['tasks_failed']}",
            f"Success rate: {summary['success_rate']:.1f}%",
            f"Total LLM calls: {summary['total_llm_calls']}",
            f"Total tokens: {summary['total_tokens']:.0f}",
        ]
        return "\n".join(lines)
