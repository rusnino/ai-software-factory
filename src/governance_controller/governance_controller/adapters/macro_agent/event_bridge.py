"""Translate macro-agent workspace events into Controller state updates."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.state_machine import StateMachine
from governance_controller.services.verification_service import VerificationService


def _parse_event_timestamp(value: Any) -> datetime | None:
    """Normalize an event timestamp to a timezone-aware datetime, if possible.

    Accepts ISO-8601 strings (with or without explicit timezone) and existing
    datetime objects. Returns ``None`` for other inputs so the event is treated
    as non-deduplicatable rather than rejected.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


_EVENT_TO_STATE: dict[str, TaskState] = {
    "worktree:allocated": TaskState.RUNNING,
    "landing:started": TaskState.RUNNING,
    "landing:completed": TaskState.AGENT_REVIEW,
    "stream:committed": TaskState.RUNNING,
    "mergeQueue:added": TaskState.RUNNING,
    "mergeQueue:ready": TaskState.RUNNING,
    "conflict:created": TaskState.BLOCKED,
    "conflict:resolved": TaskState.RUNNING,
    "stream:abandoned": TaskState.FAILED,
}


class EventBridge:
    """In-process bridge from macro-agent workspace events to controller state."""

    _event_to_state = _EVENT_TO_STATE

    @staticmethod
    async def handle(
        db: AsyncSession,
        event: dict[str, Any],
        verification_service: VerificationService | None = None,
    ) -> None:
        """Process a single macro-agent workspace event.

        Args:
            db: The SQLAlchemy async session to use.
            event: The macro-agent workspace event. Must contain ``type`` and
                ``metadata.controller_task_id``.
            verification_service: Optional service used to verify the task when
                a ``landing:completed`` event moves it to ``AGENT_REVIEW``.
        """
        event_type = event.get("type", "")
        metadata = event.get("metadata") or {}
        task_id = metadata.get("controller_task_id")

        # SPEC-05 §5.4: idempotency on (task_id, event_type,
        # event_timestamp, event_id). Events without these fields are logged
        # but cannot be deduplicated, which matches the historical behavior.
        event_id = metadata.get("event_id")
        event_timestamp = _parse_event_timestamp(metadata.get("event_timestamp"))

        if event_id and event_timestamp and task_id:
            processed = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.task_id == task_id,
                    ProcessedEvent.event_type == event_type,
                    ProcessedEvent.event_timestamp == event_timestamp,  # type: ignore[arg-type]
                    ProcessedEvent.event_id == event_id,
                )
            )
            if processed.scalar_one_or_none() is not None:
                return

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
            valid_transition = True
            try:
                StateMachine.validate_transition(task.state, target_state)
            except ValueError:
                valid_transition = False

            if valid_transition:
                if not await StateMachine.atomic_transition(
                    db, task, target_state
                ):
                    raise ValueError(
                        "Concurrent modification detected: "
                        "task state changed during event handling"
                    )
                # Re-load the task so the in-memory object reflects the latest
                # DB state after our own successful update.
                task = await db.get(Task, task_id)
                if task is None:
                    return
            else:
                transition_error = (
                    f"Invalid transition: {task.state.value} -> "
                    f"{target_state.value}"
                )

        # Phase 1: landing:completed triggers automated verification that gates
        # AGENT_REVIEW -> HUMAN_REVIEW. This is a stub-grade integration until
        # real CI/artifact checks exist in Phase 2. Skip verification if the
        # task has no stored contract (e.g., tests that only exercise state
        # transitions).
        if (
            transition_error is None
            and target_state == TaskState.AGENT_REVIEW
            and task.state == TaskState.AGENT_REVIEW
            and task.task_contract_json
        ):
            verifier = verification_service or VerificationService()
            contract = TaskContract(
                **cast(dict[str, Any], task.task_contract_json)
            )
            await verifier.verify_and_advance(db, task, contract)

        # Record the event as processed only after transitions and
        # verification succeed. A missing event_id/event_timestamp means we
        # cannot deduplicate, so we persist only when the full key is present.
        if event_id and event_timestamp:
            await EventBridge._record_processed_event(
                db,
                task_id=task_id,
                event_type=event_type,
                event_timestamp=event_timestamp,
                event_id=event_id,
            )

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

    @staticmethod
    async def _record_processed_event(
        db: AsyncSession,
        *,
        task_id: str,
        event_type: str,
        event_timestamp: datetime,
        event_id: str,
    ) -> None:
        """Persist the processed-event key so replays are ignored.

        Because callers run inside an existing transaction, an integrity error
        would abort the entire session. We therefore use an upsert that is a
        no-op when the key already exists. This works for PostgreSQL; SQLite
        tests use one transaction and cannot concurrently duplicate the insert
        anyway.
        """
        try:
            dialect_name = db.bind.dialect.name if db.bind else ""
        except AttributeError:
            dialect_name = ""

        if dialect_name == "postgresql":
            stmt = (
                pg_insert(ProcessedEvent)
                .values(
                    task_id=task_id,
                    event_type=event_type,
                    event_timestamp=event_timestamp,
                    event_id=event_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "task_id",
                        "event_type",
                        "event_timestamp",
                        "event_id",
                    ]
                )
            )
            await db.execute(stmt)
        else:
            # SQLite fallback: check-then-add to keep the test transaction
            # alive after a duplicate is handled by the caller.
            existing = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.task_id == task_id,  # type: ignore[arg-type]
                    ProcessedEvent.event_type == event_type,  # type: ignore[arg-type]
                    ProcessedEvent.event_timestamp == event_timestamp,  # type: ignore[arg-type]
                    ProcessedEvent.event_id == event_id,  # type: ignore[arg-type]
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(
                    ProcessedEvent(
                        task_id=task_id,
                        event_type=event_type,
                        event_timestamp=event_timestamp,
                        event_id=event_id,
                    )
                )
