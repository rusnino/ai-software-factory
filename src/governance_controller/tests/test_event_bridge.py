"""Tests for the macro-agent event bridge listener."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.event_bridge import (
    _EVENT_TO_STATE,
    EventBridge,
)
from governance_controller.constants import TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task


def _make_event(event_type: str, task_id: str | None) -> dict:
    metadata: dict = {}
    if task_id is not None:
        metadata["controller_task_id"] = task_id
    return {"type": event_type, "metadata": metadata, "payload": {"foo": "bar"}}


class TestEventBridgeMapping:
    def test_event_to_state_mapping_contains_required_keys(self) -> None:
        assert _EVENT_TO_STATE["worktree:allocated"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["landing:started"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["landing:completed"] == TaskState.AGENT_REVIEW
        assert _EVENT_TO_STATE["conflict:created"] == TaskState.BLOCKED
        assert _EVENT_TO_STATE["conflict:resolved"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["stream:abandoned"] == TaskState.FAILED


class TestEventBridgeTransitions:
    async def test_landing_completed_transitions_running_to_agent_review(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-1",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.AGENT_REVIEW

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_landing:completed"
        assert entries[0].payload == event

    async def test_conflict_created_transitions_running_to_blocked(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-2",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("conflict:created", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.BLOCKED

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_conflict:created"

    async def test_unknown_event_is_logged_but_state_unchanged(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-3",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("workspace:custom", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.RUNNING

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_other"
        assert entries[0].payload == event

    async def test_missing_task_id_does_not_raise_and_logs_other_event(
        self,
        db_session: AsyncSession,
    ) -> None:
        event = {"type": "landing:completed", "metadata": {}}

        await EventBridge.handle(db_session, event)

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "macro_agent_other")
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].task_id == "unknown"
        assert entries[0].payload == event

    async def test_missing_task_logs_event_without_raising(
        self,
        db_session: AsyncSession,
    ) -> None:
        event = _make_event("landing:completed", "missing-task")

        await EventBridge.handle(db_session, event)

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == "missing-task")
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_landing:completed"
        assert entries[0].payload == event

    async def test_duplicate_event_is_idempotent(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-4",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.AGENT_REVIEW

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 2
        assert all(
            e.event_type == "macro_agent_landing:completed" for e in entries
        )

    async def test_invalid_transition_for_current_state_is_logged_but_state_unchanged(
        self,
        db_session: AsyncSession,
    ) -> None:
        # landing:completed targets AGENT_REVIEW, but PROPOSED cannot move there.
        task = Task(
            id="task-5",
            project_id="proj-1",
            state=TaskState.PROPOSED,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.PROPOSED

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_landing:completed"
