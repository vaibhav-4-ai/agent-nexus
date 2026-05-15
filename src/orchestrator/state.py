"""
Task State Machine — enforces valid task lifecycle transitions.
"""

from __future__ import annotations

from src.infra.db import TaskStatus
from src.infra.logging import get_logger

logger = get_logger("orchestrator.state")

# Valid state transitions
TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.PARSING, TaskStatus.CANCELLED},
    TaskStatus.PARSING: {TaskStatus.PLANNING, TaskStatus.FAILED},
    TaskStatus.PLANNING: {TaskStatus.EXECUTING, TaskStatus.FAILED},
    TaskStatus.EXECUTING: {TaskStatus.VERIFYING, TaskStatus.RECOVERING, TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.VERIFYING: {TaskStatus.EXECUTING, TaskStatus.RECOVERING, TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.RECOVERING: {TaskStatus.EXECUTING, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


class TaskStateMachine:
    """
    State machine for task lifecycle management.

    Enforces valid transitions and logs state changes.
    """

    def __init__(self, task_id: str, initial_state: TaskStatus = TaskStatus.PENDING) -> None:
        self.task_id = task_id
        self._state = initial_state

    @property
    def state(self) -> TaskStatus:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def can_transition(self, new_state: TaskStatus) -> bool:
        """Check if a transition is valid."""
        return new_state in TRANSITIONS.get(self._state, set())

    def transition(self, new_state: TaskStatus) -> None:
        """Transition to a new state, raising ValueError if invalid."""
        if not self.can_transition(new_state):
            raise ValueError(
                f"Invalid transition: {self._state.value} -> {new_state.value} "
                f"(allowed: {[s.value for s in TRANSITIONS.get(self._state, set())]})"
            )
        old = self._state
        self._state = new_state
        logger.info(
            "task_state_transition",
            task_id=self.task_id,
            from_state=old.value,
            to_state=new_state.value,
        )
