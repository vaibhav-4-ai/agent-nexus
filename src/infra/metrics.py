"""
Custom metrics collector (replaces Prometheus + Grafana for free tier).

Stores metrics in Postgres and optionally ships to DagsHub MLflow.
Provides a clean API for recording counters, histograms, and gauges.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger

logger = get_logger("infra.metrics")


class MetricsCollector:
    """
    Lightweight metrics collector that stores data in Postgres and optionally MLflow.

    Tracks the same metrics as the original Prometheus spec:
    - agent_tasks_total (counter)
    - agent_task_duration_seconds (histogram)
    - agent_steps_total (counter)
    - agent_llm_calls_total (counter)
    - agent_llm_tokens_total (counter)
    - agent_verification_results (counter)
    - agent_mcp_tool_calls (counter)
    """

    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._mlflow_enabled = False
        self._db_persist_enabled = True

    async def initialize(self) -> None:
        """Initialize MLflow connection if configured."""
        settings = get_settings()
        if settings.monitoring.mlflow_tracking_uri:
            try:
                import mlflow
                dagshub_token = settings.monitoring.dagshub_token.get_secret_value()
                if dagshub_token:
                    import os
                    os.environ["MLFLOW_TRACKING_USERNAME"] = dagshub_token
                    os.environ["MLFLOW_TRACKING_PASSWORD"] = dagshub_token

                mlflow.set_tracking_uri(settings.monitoring.mlflow_tracking_uri)
                mlflow.set_experiment(settings.monitoring.mlflow_experiment_name)
                self._mlflow_enabled = True
                logger.info("mlflow_connected", uri=settings.monitoring.mlflow_tracking_uri)
            except Exception as e:
                logger.warning("mlflow_init_failed", error=str(e))

    async def increment(self, metric: str, value: float = 1.0,
                        labels: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        key = self._make_key(metric, labels)
        self._counters[key] += value

        if self._mlflow_enabled:
            try:
                import mlflow
                mlflow.log_metric(metric, self._counters[key])
            except Exception:
                pass  # Non-critical

    async def record_duration(self, metric: str, duration_ms: float,
                              labels: dict[str, str] | None = None) -> None:
        """Record a duration (histogram-style)."""
        await self.increment(f"{metric}_count", 1.0, labels)
        await self.increment(f"{metric}_sum_ms", duration_ms, labels)

    async def record_tokens(self, provider: str, input_tokens: int,
                            output_tokens: int) -> None:
        """Record LLM token usage."""
        await self.increment("agent_llm_tokens_total",
                             float(input_tokens), {"provider": provider, "direction": "input"})
        await self.increment("agent_llm_tokens_total",
                             float(output_tokens), {"provider": provider, "direction": "output"})

    async def record_tool_call(self, server: str, tool: str,
                               success: bool, duration_ms: float) -> None:
        """Record an MCP tool call."""
        status = "success" if success else "error"
        await self.increment("agent_mcp_tool_calls",
                             labels={"server": server, "tool": tool, "status": status})
        await self.record_duration("agent_step_duration", duration_ms,
                                   {"tool": tool})

    async def record_verification(self, result: str) -> None:
        """Record a verification result (pass/retry/rollback/escalate)."""
        await self.increment("agent_verification_results", labels={"result": result})

    def get_all_metrics(self) -> dict[str, float]:
        """Get all current metric values (for the dashboard API)."""
        return dict(self._counters)

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of key metrics for the dashboard."""
        metrics = self._counters
        tasks_completed = metrics.get("agent_tasks_total|status=completed", 0)
        tasks_failed = metrics.get("agent_tasks_total|status=failed", 0)
        total_tasks = tasks_completed + tasks_failed

        return {
            "total_tasks": total_tasks,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "success_rate": (tasks_completed / total_tasks * 100) if total_tasks > 0 else 0,
            "total_llm_calls": metrics.get("agent_llm_calls_total", 0),
            "total_tokens": sum(v for k, v in metrics.items() if k.startswith("agent_llm_tokens")),
            "verifications_passed": metrics.get("agent_verification_results|result=pass", 0),
            "verifications_failed": metrics.get("agent_verification_results|result=retry", 0)
            + metrics.get("agent_verification_results|result=rollback", 0),
        }

    @staticmethod
    def _make_key(metric: str, labels: dict[str, str] | None) -> str:
        """Create a unique key from metric name + labels."""
        if not labels:
            return metric
        label_str = "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{metric}|{label_str}"


# Module-level singleton
_metrics: MetricsCollector | None = None


async def get_metrics() -> MetricsCollector:
    """Get the singleton MetricsCollector."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
        await _metrics.initialize()
    return _metrics
