"""Deterministic state machine for Task transitions."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

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
        },
        TaskState.BLOCKED: {TaskState.RUNNING, TaskState.FAILED},
        TaskState.FAILED: set(),
        TaskState.DONE: set(),
    }
    # Retry after a failed verification is intentionally NOT in the global
    # transition table. It is scoped to VerificationService so that arbitrary
    # macro-agent events cannot resurrect a terminal FAILED task.

    @classmethod
    def _validate(cls, current_state: TaskState, target_state: TaskState) -> None:
        """Raise ValueError if *current_state* -> *target_state* is not allowed."""
        allowed = cls._transitions.get(current_state, set())
        if target_state not in allowed:
            raise ValueError(
                f"Invalid transition: {current_state.value} -> {target_state.value}"
            )

    @classmethod
    def validate_transition(
        cls,
        current_state: TaskState,
        target_state: TaskState,
    ) -> None:
        """Validate a transition without mutating a task.

        Args:
            current_state: The state before the transition.
            target_state: The desired state.

        Raises:
            ValueError: If the transition is not valid.
        """
        cls._validate(current_state, target_state)

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
        cls._validate(task.state, target_state)

        task.state = target_state
        task.updated_at = datetime.now(UTC)
        return task

    @classmethod
    async def atomic_transition(
        cls,
        db: AsyncSession,
        task: Task,
        target_state: TaskState,
    ) -> bool:
        """Atomically transition *task* to *target_state* using an UPDATE WHERE.

        The database update only succeeds when the task's current state and
        version match the in-memory object. This prevents concurrent callers
        from silently overwriting each other's state changes.

        Args:
            db: The SQLAlchemy async session.
            task: The task instance holding the expected current state/version.
            target_state: The desired state.

        Returns:
            True if the update succeeded, False if the task was already
            modified by another transaction or the transition is invalid.
        """
        try:
            cls._validate(task.state, target_state)
        except ValueError:
            return False

        result = await db.execute(
            update(Task)
            .where(
                Task.id == task.id,  # type: ignore[arg-type]
                Task.version == task.version,  # type: ignore[arg-type]
                Task.state == task.state.value,  # type: ignore[arg-type]
            )
            .values(
                state=target_state.value,
                version=task.version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:  # type: ignore[attr-defined]
            task.state = target_state
            task.version = task.version + 1
            task.updated_at = datetime.now(UTC)
            return True
        return False

    @classmethod
    async def atomic_transition_from_failed_to_running(
        cls,
        db: AsyncSession,
        task: Task,
        execution_attempts: int,
    ) -> bool:
        """Scoped retry transition: FAILED -> RUNNING with execution_attempts.

        This is intentionally separate from the generic transition table so
        that only the verification-retry path can resurrect a terminal FAILED
        task. The WHERE clause checks the pre-state is FAILED explicitly.
        """
        if task.state is not TaskState.FAILED:
            return False

        result = await db.execute(
            update(Task)
            .where(
                Task.id == task.id,  # type: ignore[arg-type]
                Task.version == task.version,  # type: ignore[arg-type]
                Task.state == TaskState.FAILED.value,  # type: ignore[arg-type]
            )
            .values(
                state=TaskState.RUNNING.value,
                version=task.version + 1,
                execution_attempts=execution_attempts,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:  # type: ignore[attr-defined]
            task.state = TaskState.RUNNING
            task.version = task.version + 1
            task.execution_attempts = execution_attempts
            task.updated_at = datetime.now(UTC)
            return True
        return False

