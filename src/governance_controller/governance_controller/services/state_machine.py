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
        # Failed verification may return to RUNNING for retry per SPEC-09 §9.6.
        TaskState.FAILED: {TaskState.RUNNING},
        TaskState.DONE: set(),
    }

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
    async def atomic_transition_with_fields(
        cls,
        db: AsyncSession,
        task: Task,
        target_state: TaskState,
        **field_values: object,
    ) -> bool:
        """Atomically transition *task* and update additional fields in one UPDATE."""
        try:
            cls._validate(task.state, target_state)
        except ValueError:
            return False

        values: dict[str, object] = {
            "state": target_state.value,
            "version": task.version + 1,
            "updated_at": datetime.now(UTC),
        }
        values.update(field_values)
        result = await db.execute(
            update(Task)
            .where(
                Task.id == task.id,  # type: ignore[arg-type]
                Task.version == task.version,  # type: ignore[arg-type]
                Task.state == task.state.value,  # type: ignore[arg-type]
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:  # type: ignore[attr-defined]
            task.state = target_state
            task.version = task.version + 1
            task.updated_at = values["updated_at"]  # type: ignore[assignment]
            for name, value in field_values.items():
                setattr(task, name, value)
            return True
        return False
