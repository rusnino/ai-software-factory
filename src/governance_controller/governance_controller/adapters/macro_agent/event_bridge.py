"""Translate macro-agent workspace events into Controller state updates."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
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
        actor: str = "macro-agent",
    ) -> None:
        """Process a single macro-agent workspace event.

        Args:
            db: The SQLAlchemy async session to use.
            event: The macro-agent workspace event. Must contain ``type`` and
                ``metadata.controller_task_id``.
            verification_service: Optional service used to verify the task when
                a ``landing:completed`` event moves it to ``AGENT_REVIEW``.
            actor: The authenticated caller identity to record in the audit log.
                ``conflict:resolved`` events supply a ``human:<email>`` actor
                because they require a separate human-scoped credential.
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
                actor=actor,
                source="macro-agent",
                payload=event,
            )
            return

        target_state = _EVENT_TO_STATE.get(event_type)
        audit_event_type = (
            "macro_agent_other" if target_state is None else f"macro_agent_{event_type}"
        )

        result = await db.execute(select(Task).where(Task.id == task_id))
        task: Task | None = result.scalar_one_or_none()

        if task is None:
            await AuditService.log(
                db=db,
                event_type=audit_event_type,
                task_id=task_id,
                actor=actor,
                source="macro-agent",
                payload=event,
            )
            return

        transition_error: str | None = None
        if target_state is not None and task.state != target_state:
            # GAP-099: Only conflict:resolved is allowed to unblock a BLOCKED
            # task. Other RUNNING-mapped events must be rejected so a stray
            # worktree or merge-queue event cannot silently resume blocked
            # work.
            if task.state == TaskState.BLOCKED and event_type != "conflict:resolved":
                transition_error = (
                    f"Blocked task may only be unblocked by conflict:resolved; "
                    f"got {event_type}"
                )
            else:
                valid_transition = True
                try:
                    StateMachine.validate_transition(task.state, target_state)
                except ValueError:
                    valid_transition = False

                if valid_transition:
                    if not await StateMachine.atomic_transition(db, task, target_state):
                        await AuditService.log(
                            db=db,
                            event_type="concurrent_modification",
                            task_id=task_id,
                            actor=actor,
                            source="macro-agent",
                            payload={
                                "expected_state": task.state.value,
                                "target_state": target_state.value,
                                "context": f"event_bridge:{event_type}",
                                "event": event,
                            },
                        )
                        await db.commit()
                        raise ValueError(
                            "Concurrent modification detected: "
                            "task state changed during event handling"
                        )
                    # Re-load the task so the in-memory object reflects the
                    # latest DB state after our own successful update.
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
        # task has no stored contract (e.g. tests that only exercise state
        # transitions).
        if (
            transition_error is None
            and target_state == TaskState.AGENT_REVIEW
            and task.state == TaskState.AGENT_REVIEW
            and task.task_contract_json
        ):
            # Commit before verification so no task row lock is held across the
            # potentially slow subprocess execution (GAP-095).
            await db.commit()

            # Write a durable "verification in progress" marker *before* the
            # slow verification runs. A duplicate delivery that fails the
            # uniqueness check is short-circuited here so verification runs
            # exactly once. We roll the marker back only if this call lost the
            # race and no state transition occurred, preserving the #118 retry
            # semantics.
            in_progress_key: dict[str, Any] | None = None
            won_marker = False
            if event_id and event_timestamp:
                in_progress_key = {
                    "task_id": task_id,
                    "event_type": event_type,
                    "event_timestamp": event_timestamp,
                    "event_id": event_id,
                }
                won_marker = await EventBridge._record_processed_event(
                    db, **in_progress_key
                )
                await db.commit()
                if not won_marker:
                    # Another call is processing this event right now. Return
                    # silently and let the first call complete/audit.
                    return

            verifier = verification_service or VerificationService()
            contract = TaskContract(**cast(dict[str, Any], task.task_contract_json))
            # Profile is optional in Phase 1; retry execution falls back to
            # defaults if no profile is available.
            profile = None
            if task.project_id:
                from governance_controller.services.task_service import (
                    TaskService,
                )

                profile = await TaskService(db).get_profile_by_project_id(
                    task.project_id
                )

            # Capture the state before verification so we can tell whether this
            # delivery actually advanced the task. If verify_and_advance raises
            # before any transition (e.g. a non-existent guessed worktree path),
            # the dedup key must be removed so the event can be redelivered
            # (#118).
            state_before_verify = task.state
            try:
                await verifier.verify_and_advance(
                    db, task, contract, profile=profile, executor=verifier.executor
                )
            except Exception:
                # Re-load the task with populate_existing so the identity map
                # cannot hide a transition committed by another session. If this
                # call did not actually win the state change, delete the marker
                # so a legitimate sequential redelivery can retry.
                fresh_result = await db.execute(
                    select(Task)
                    .where(Task.id == task_id)
                    .execution_options(populate_existing=True)
                )
                fresh_task: Task | None = fresh_result.scalar_one_or_none()
                transition_happened = (
                    fresh_task is not None and fresh_task.state != state_before_verify
                )
                if (
                    not transition_happened
                    and won_marker
                    and in_progress_key is not None
                ):
                    await db.execute(
                        delete(ProcessedEvent).where(
                            ProcessedEvent.task_id == task_id,
                            ProcessedEvent.event_type == event_type,
                            ProcessedEvent.event_timestamp == event_timestamp,  # type: ignore[arg-type]
                            ProcessedEvent.event_id == event_id,  # type: ignore[arg-type]
                        )
                    )
                    await db.commit()
                raise

        # Record the event as processed so a replay is ignored regardless of
        # whether verification passed, failed terminally, failed with a retry
        # scheduled, or was skipped because no contract was stored. A missing
        # event_id/event_timestamp means we cannot deduplicate, so we persist
        # only when the full key is present.
        if event_id and event_timestamp:
            await EventBridge._record_processed_event(
                db,
                task_id=task_id,
                event_type=event_type,
                event_timestamp=event_timestamp,
                event_id=event_id,
            )
            await db.flush()

        # A verification failure with remaining retries transitions the task
        # back to RUNNING. The processed-event key has already been recorded,
        # so a replayed landing:completed cannot re-enter the retry path.
        if target_state == TaskState.AGENT_REVIEW and task.state == TaskState.RUNNING:
            return

        payload: dict[str, Any] = {"event": event}
        if transition_error is not None:
            payload["transition_error"] = transition_error
            await AuditService.log(
                db=db,
                event_type=audit_event_type,
                task_id=task_id,
                actor=actor,
                source="macro-agent",
                payload=payload,
            )
            await db.commit()
            # Surface the guard rejection as a non-204 signal so callers can
            # tell their event did not take effect, instead of returning the
            # same success status as a processed event (#279).
            raise ValueError(f"Event rejected: {transition_error}")

        await AuditService.log(
            db=db,
            event_type=audit_event_type,
            task_id=task_id,
            actor=actor,
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
    ) -> bool:
        """Persist the processed-event key so replays are ignored.

        Returns True if a row was actually inserted, False if the key already
        existed. Because callers run inside an existing transaction, an
        integrity error would abort the entire session. We therefore use an
        upsert that is a no-op when the key already exists on Postgres. SQLite
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
            result = await db.execute(stmt)
            return bool(getattr(result, "rowcount", None))
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
                return True
            return False
