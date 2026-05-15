"""
Orchestrator Engine — the core execution loop.

This is the brain of agent-nexus. It ties together goal parsing, planning,
tool execution, memory, verification, and self-healing recovery.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from src.infra.db import TaskRepository, TaskStatus, StepStatus, get_session
from src.infra.event_bus import EventBus, EventType
from src.infra.logging import Timer, bind_task_context, get_logger
from src.infra.metrics import get_metrics
from src.memory.cag_manager import CAGManager
from src.memory.episodic import EpisodicMemory
from src.memory.graph_memory import GraphMemory
from src.memory.memory_router import MemoryRouter
from src.memory.rag_engine import RAGEngine
from src.mcp.client import MCPClient
from src.orchestrator.executor import Executor
from src.orchestrator.goal_parser import GoalParser
from src.orchestrator.planner import Planner
from src.orchestrator.state import TaskStateMachine
from src.verification.evidence_collector import EvidenceCollector
from src.verification.recovery import RecoveryEngine
from src.verification.verifier import Verifier

logger = get_logger("orchestrator.engine")


class OrchestratorEngine:
    """
    The main orchestration engine.

    Executes the full task lifecycle:
    parse → plan → (execute → verify → recover)* → complete
    """

    def __init__(self, mcp_client: MCPClient, event_bus: EventBus) -> None:
        self._mcp = mcp_client
        self._event_bus = event_bus
        self._goal_parser = GoalParser()
        self._planner = Planner()
        self._executor = Executor(mcp_client)
        self._verifier = Verifier()
        self._evidence_collector = EvidenceCollector()
        self._recovery = RecoveryEngine()
        self._rag = RAGEngine()
        self._episodic = EpisodicMemory()
        self._graph = GraphMemory()

    async def execute_task(self, task_id: str, goal: str,
                            attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Execute a complete task from goal to result."""
        bind_task_context(task_id)
        start_time = time.perf_counter()
        metrics = await get_metrics()
        state = TaskStateMachine(task_id)
        cag = CAGManager()
        memory_router = MemoryRouter(cag, self._rag, self._episodic, self._graph)
        execution_trace: list[dict[str, Any]] = []

        try:
            # === 1. Parse Goal ===
            state.transition(TaskStatus.PARSING)
            await self._event_bus.emit(EventType.TASK_STARTED, {"goal": goal}, task_id)
            parsed_goal = await self._goal_parser.parse(goal, attachments)
            cag.update("system", f"Goal parsed: {parsed_goal.objective}")

            # === 2. Check Episodic Memory ===
            memory_ctx = await memory_router.get_context(parsed_goal.objective, goal)

            # === 3. Create Plan ===
            state.transition(TaskStatus.PLANNING)
            plan = await self._planner.create_plan(
                parsed_goal,
                available_tools=self._mcp.get_tools_display(),
                past_experience=memory_ctx.episodic_context,
                context=memory_ctx.cag_context,
            )
            cag.update("plan", f"Plan: {plan.plan_summary}")

            # === 4. Execute Plan Steps ===
            state.transition(TaskStatus.EXECUTING)
            completed_steps = 0

            for step in plan.steps:
                step_trace: dict[str, Any] = {
                    "step_number": step.step_number,
                    "description": step.description,
                    "status": "pending",
                }

                await self._event_bus.emit(
                    EventType.STEP_STARTED,
                    {"step": step.step_number, "description": step.description},
                    task_id,
                )

                # Get step-level memory context
                step_ctx = await memory_router.get_context(step.description, goal)

                # Execute with retry loop
                retry_count = 0
                step_success = False

                while not step_success and retry_count <= RecoveryEngine.MAX_RETRIES:
                    # Select and invoke tool
                    selection, result = await self._executor.execute_step(
                        step.description,
                        step.expected_outcome,
                        context=step_ctx.to_prompt(),
                    )
                    step_trace["tool"] = selection.tool_name
                    step_trace["tool_args"] = selection.arguments

                    # Collect evidence
                    evidence = await self._evidence_collector.collect(
                        selection.tool_name, result, step.description
                    )

                    # Verify
                    state.transition(TaskStatus.VERIFYING)
                    verification = await self._verifier.verify(
                        step.description, step.expected_outcome,
                        selection.tool_name, evidence,
                    )

                    if verification.confidence > 0.8 and verification.verified:
                        # Success!
                        step_success = True
                        step_trace["status"] = "completed"
                        step_trace["result"] = result.content[:500]
                        step_trace["verification"] = {"confidence": verification.confidence}
                        cag.update("result", f"Step {step.step_number}: {result.content[:200]}")
                        completed_steps += 1
                        state.transition(TaskStatus.EXECUTING)
                        await self._event_bus.emit(
                            EventType.STEP_COMPLETED,
                            {"step": step.step_number, "confidence": verification.confidence},
                            task_id,
                        )
                    else:
                        # Failed — decide recovery
                        state.transition(TaskStatus.RECOVERING)
                        recovery = await self._recovery.decide(
                            step.description, selection.tool_name,
                            verification.evidence_summary, verification, retry_count,
                        )

                        if recovery.strategy == "retry":
                            retry_count += 1
                            state.transition(TaskStatus.EXECUTING)
                        elif recovery.strategy == "skip":
                            step_trace["status"] = "skipped"
                            step_trace["skip_reason"] = recovery.reasoning
                            state.transition(TaskStatus.EXECUTING)
                            break
                        elif recovery.strategy == "escalate":
                            step_trace["status"] = "escalated"
                            state.transition(TaskStatus.EXECUTING)
                            break
                        else:  # rollback
                            retry_count += 1
                            state.transition(TaskStatus.EXECUTING)

                if not step_success and step_trace.get("status") == "pending":
                    step_trace["status"] = "failed"

                execution_trace.append(step_trace)
                await asyncio.sleep(0)  # Yield for streaming

            # === 5. Store in Episodic Memory ===
            what_worked = [s["description"] for s in execution_trace if s.get("status") == "completed"]
            what_failed = [s["description"] for s in execution_trace if s.get("status") == "failed"]

            try:
                await self._episodic.store_execution({
                    "id": task_id,
                    "goal": goal,
                    "status": "completed" if completed_steps > 0 else "failed",
                    "steps_count": len(plan.steps),
                    "completed_steps": completed_steps,
                    "what_worked": what_worked[:5],
                    "what_failed": what_failed[:5],
                })
            except Exception as e:
                logger.warning("episodic_store_failed", error=str(e))

            # === 6. Return Result ===
            duration_ms = (time.perf_counter() - start_time) * 1000
            status = TaskStatus.COMPLETED if completed_steps > 0 else TaskStatus.FAILED
            state.transition(status)

            await metrics.increment("agent_tasks_total", labels={"status": status.value})
            await metrics.record_duration("agent_task_duration", duration_ms)
            await self._event_bus.emit(
                EventType.TASK_COMPLETED, {"status": status.value, "duration_ms": duration_ms}, task_id
            )

            return {
                "task_id": task_id,
                "status": status.value,
                "goal": goal,
                "plan_summary": plan.plan_summary,
                "total_steps": len(plan.steps),
                "completed_steps": completed_steps,
                "execution_trace": execution_trace,
                "duration_ms": round(duration_ms, 2),
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error("task_execution_failed", task_id=task_id, error=str(e))
            await metrics.increment("agent_tasks_total", labels={"status": "failed"})
            await self._event_bus.emit(
                EventType.TASK_FAILED, {"error": str(e)}, task_id
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "error": str(e),
                "execution_trace": execution_trace,
                "duration_ms": round(duration_ms, 2),
            }
