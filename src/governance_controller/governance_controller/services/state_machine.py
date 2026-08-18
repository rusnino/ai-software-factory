"""Deterministic state machine for Task transitions."""

from datetime import UTC, datetime

from governance_controller.constants import TaskState
from governance_controller.models.task import Task


class StateMachine:
    """Deterministic state machine governing all valid Task transitions."""

    _transitions: dict[TaskState, set[TaskState]] = {
        TaskState.PROPOSED: {TaskState.PLAN_APPROVED},
        TaskState.PLAN_APPROVED: {TaskState.EXEC_APPROVED},
        TaskState.EXEC_APPROVED: {TaskState.READY},
        TaskState.READY: {TaskState.RUNNING, TaskState.FAILED},
        TaskState.RUNNING: {
            TaskState.AGENT_REVIEW,
            TaskState.BLOCKED,
            TaskState.FAILED,
        },
        TaskState.AGENT_REVIEW: {
            TaskState.HUMAN_REVIEW,
            TaskState.BLOCKED,
            TaskState.FAILED,
        },
        TaskState.HUMAN_REVIEW: {
            TaskState.DONE,
            TaskState.FAILED,
            TaskState.RUNNING,
        },
        TaskState.BLOCKED: {TaskState.RUNNING, TaskState.FAILED},
        TaskState.FAILED: set(),
        TaskState.DONE: set(),
    }

    @classmethod
    def transition(cls, task: Task, target_state: TaskState) -> Task:
        """Transition *task* to *target_state* if valid.

        Args:
            task: The task to transition.
            target_state: The desired state.

        Returns:
            The same task instance, updated.

        Raises:
            ValueError: If the transition is not valid.
        """
        current_state = task.state
        allowed = cls._transitions.get(current_state, set())

        if target_state not in allowed:
            raise ValueError(
                f"Invalid transition: {current_state.value} -> {target_state.value}"
            )

        task.state = target_state
        task.updated_at = datetime.now(UTC)
        return task
