"""Translate macro-agent workspace events into Controller state updates."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.models.task import Task
from governance_controller.services.audit_service import AuditService
from governance_controller.services.state_machine import StateMachine

_EVENT_TO_STATE: dict[str, TaskState] = {
    "worktree:allocated": TaskState.RUNNING,
    "landing:started": TaskState.RUNNING,
    "landing:completed": TaskState.AGENT_REVIEW,
    "conflict:created": TaskState.BLOCKED,
    "conflict:resolved": TaskState.RUNNING,
    "stream:abandoned": TaskState.FAILED,
}


class EventBridge:
    """In-process bridge from macro-agent workspace events to controller state."""

    _event_to_state = _EVENT_TO_STATE

    @staticmethod
    async def handle(db: AsyncSession, event: dict[str, Any]) -> None:
        """Process a single macro-agent workspace event.

        Args:
            db: The SQLAlchemy async session to use.
            event: The macro-agent workspace event. Must contain ``type`` and
                ``metadata.controller_task_id``.
        """
        event_type = event.get("type", "")
        metadata = event.get("metadata") or {}
        task_id = metadata.get("controller_task_id")

        if not task_id:
            await AuditService.log(
                db=db,
                event_type="macro_agent_other",
                task_id="unknown",
                actor="macro-agent",
                source="macro-agent",
                payload=event,
            )
            return

        target_state = _EVENT_TO_STATE.get(event_type)
        audit_event_type = (
            "macro_agent_other"
            if target_state is None
            else f"macro_agent_{event_type}"
        )

        result = await db.execute(select(Task).where(Task.id == task_id))
        task: Task | None = result.scalar_one_or_none()

        if task is None:
            await AuditService.log(
                db=db,
                event_type=audit_event_type,
                task_id=task_id,
                actor="macro-agent",
                source="macro-agent",
                payload=event,
            )
            return

        transition_error: str | None = None
        if target_state is not None and task.state != target_state:
            try:
                StateMachine.transition(task, target_state)
            except ValueError as exc:
                transition_error = str(exc)

        payload: dict[str, Any] = {"event": event}
        if transition_error is not None:
            payload["transition_error"] = transition_error

        await AuditService.log(
            db=db,
            event_type=audit_event_type,
            task_id=task_id,
            actor="macro-agent",
            source="macro-agent",
            payload=payload,
        )
