"""Tests for the macro-agent event bridge listener."""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.event_bridge import (
    _EVENT_TO_STATE,
    EventBridge,
)
from governance_controller.constants import TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas import (
    Check,
    CompletionContract,
    ForbiddenPathCheck,
    ScopeCheck,
)
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.verification_service import VerificationService


class _CommitCountingAsyncSession:
    """Wrapper that counts explicit commits on an AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.commit_count = 0

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    async def commit(self) -> None:
        self.commit_count += 1
        await self._session.commit()


def _make_event(
    event_type: str,
    task_id: str | None,
    *,
    event_id: str | None = "evt-1",
    event_timestamp: str | None = "2026-08-18T00:00:00+00:00",
) -> dict:
    metadata: dict = {}
    if task_id is not None:
        metadata["controller_task_id"] = task_id
    if event_id is not None:
        metadata["event_id"] = event_id
    if event_timestamp is not None:
        metadata["event_timestamp"] = event_timestamp
    return {"type": event_type, "metadata": metadata, "payload": {"foo": "bar"}}


class TestEventBridgeMapping:
    def test_event_to_state_mapping_contains_required_keys(self) -> None:
        assert _EVENT_TO_STATE["worktree:allocated"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["landing:started"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["landing:completed"] == TaskState.AGENT_REVIEW
        assert _EVENT_TO_STATE["stream:committed"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["mergeQueue:added"] == TaskState.RUNNING
        assert _EVENT_TO_STATE["mergeQueue:ready"] == TaskState.RUNNING
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
        assert entries[0].payload["event"] == event
        assert "transition_error" not in entries[0].payload

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

    async def test_stream_committed_transitions_ready_to_running(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-stream-committed",
            project_id="proj-1",
            state=TaskState.READY,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("stream:committed", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.RUNNING

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_stream:committed"
        assert "transition_error" not in entries[0].payload

    async def test_merge_queue_added_transitions_ready_to_running(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-merge-queue-added",
            project_id="proj-1",
            state=TaskState.READY,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("mergeQueue:added", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.RUNNING

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_mergeQueue:added"
        assert "transition_error" not in entries[0].payload

    async def test_merge_queue_ready_transitions_ready_to_running(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-merge-queue-ready",
            project_id="proj-1",
            state=TaskState.READY,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("mergeQueue:ready", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.RUNNING

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_mergeQueue:ready"
        assert "transition_error" not in entries[0].payload

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
        assert entries[0].payload["event"] == event
        assert "transition_error" not in entries[0].payload

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
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_landing:completed"

    async def test_missing_event_id_or_timestamp_is_not_deduplicated(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-4b",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id, event_id=None)
        await EventBridge.handle(db_session, event)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.AGENT_REVIEW

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 2
        assert all(e.event_type == "macro_agent_landing:completed" for e in entries)

    async def test_distinct_event_id_is_not_deduplicated(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-4c",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event1 = _make_event("landing:completed", task.id, event_id="evt-a")
        event2 = _make_event("landing:completed", task.id, event_id="evt-b")
        await EventBridge.handle(db_session, event1)
        await EventBridge.handle(db_session, event2)

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 2

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
        with pytest.raises(ValueError, match="Event rejected"):
            await EventBridge.handle(db_session, event)

        assert task.state == TaskState.PROPOSED

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_landing:completed"
        assert "transition_error" in entries[0].payload

    async def test_landing_completed_failing_completion_contract_moves_to_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-failing-contract",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            execution_attempts=2,
            task_contract_json=TaskContract(
                task_id="task-failing-contract",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Exercise failure path through EventBridge",
                acceptance=["Task ends in FAILED when required check fails"],
                completion_contract=CompletionContract(
                    task_id="task-failing-contract",
                    required=[
                        Check(
                            type="always_fail",
                            command="exit 1",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.FAILED

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert any(e.event_type == "verification_failed" for e in entries)
        verification_failed = next(
            e for e in entries if e.event_type == "verification_failed"
        )
        assert verification_failed.actor == "system"
        assert verification_failed.source == "verification_service"
        checks = {c["name"]: c for c in verification_failed.payload["checks"]}
        assert checks["required:always_fail"]["status"] == "failed"

        # GAP-090: terminal failure must record an alert_human audit row.
        assert any(e.event_type == "alert_human" for e in entries)
        alert = next(e for e in entries if e.event_type == "alert_human")
        assert alert.payload["reason"] == "max_retries_exhausted"

    async def test_landing_completed_failing_contract_retries_and_starts_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-085 regression: a retry actually restarts the macro-agent."""
        from unittest.mock import AsyncMock

        from governance_controller.adapters.macro_agent.executor import (
            MacroAgentExecutor,
        )

        task = Task(
            id="task-retry-contract",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            execution_attempts=0,
            task_contract_json=TaskContract(
                task_id="task-retry-contract",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Exercise retry path through EventBridge",
                acceptance=["Task retries when required check fails"],
                execution={"max_retries": 2},
                completion_contract=CompletionContract(
                    task_id="task-retry-contract",
                    required=[
                        Check(
                            type="always_fail",
                            command="exit 1",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        mock_executor = AsyncMock(spec=MacroAgentExecutor)
        mock_executor.start.return_value = {"run_id": "retry-run-1"}
        verifier = VerificationService(executor=mock_executor)

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event, verification_service=verifier)

        assert task.state == TaskState.RUNNING
        assert task.execution_attempts == 1
        mock_executor.start.assert_awaited_once()

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert any(e.event_type == "verification_failed_retry" for e in entries)
        assert any(e.event_type == "retry_execution_start" for e in entries)

        # The task should have a fresh Execution row for the retry.
        from governance_controller.models.execution import Execution

        executions = await db_session.execute(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert executions.scalar_one_or_none() is not None

        # GAP-077 residual: a replay of the same landing:completed event must
        # not re-enter the retry path (no second executor.start call).
        await EventBridge.handle(db_session, event, verification_service=verifier)
        assert task.state == TaskState.RUNNING
        assert task.execution_attempts == 1
        mock_executor.start.assert_awaited_once()

    async def test_retry_executor_failure_records_dedup_key(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-097 regression: dedup key persists even if retry start fails."""
        from unittest.mock import AsyncMock

        from governance_controller.adapters.macro_agent.executor import (
            MacroAgentExecutor,
        )

        task = Task(
            id="task-retry-dedup",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            execution_attempts=0,
            task_contract_json=TaskContract(
                task_id="task-retry-dedup",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Exercise retry dedup on executor failure",
                acceptance=["Dedup key survives retry executor.start failure"],
                execution={"max_retries": 2},
                completion_contract=CompletionContract(
                    task_id="task-retry-dedup",
                    required=[
                        Check(
                            type="always_fail",
                            command="exit 1",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        calls = 0

        async def _failing_then_ok(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("macro-agent unavailable")
            return {"run_id": "retry-run-ok"}

        mock_executor = AsyncMock(spec=MacroAgentExecutor)
        mock_executor.start.side_effect = _failing_then_ok
        verifier = VerificationService(executor=mock_executor)

        event = _make_event("landing:completed", task.id)

        # First delivery: retry executor.start fails.
        with pytest.raises(RuntimeError, match="retry macro-agent start failed"):
            await EventBridge.handle(db_session, event, verification_service=verifier)

        assert task.state == TaskState.FAILED
        assert task.execution_attempts == 1
        assert calls == 1

        # Second delivery of the same event: must be deduplicated, so no
        # second executor.start call and execution_attempts stays at 1.
        await EventBridge.handle(db_session, event, verification_service=verifier)
        assert task.state == TaskState.FAILED
        assert task.execution_attempts == 1
        assert calls == 1

        # GAP-097 residual: the dedup key must survive get_db()'s rollback.
        # Simulate a fresh session and verify the ProcessedEvent row exists.
        from governance_controller.models.processed_event import ProcessedEvent

        fresh = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.task_id == task.id)
        )
        assert fresh.scalar_one_or_none() is not None

    async def test_blocked_task_cannot_be_unblocked_by_non_conflict_event(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-099 regression: only conflict:resolved may unblock BLOCKED."""
        task = Task(
            id="task-blocked-stream-committed",
            project_id="proj-1",
            state=TaskState.BLOCKED,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("stream:committed", task.id)
        with pytest.raises(ValueError, match="Event rejected"):
            await EventBridge.handle(db_session, event)

        assert task.state == TaskState.BLOCKED
        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert len(entries) == 1
        assert entries[0].event_type == "macro_agent_stream:committed"
        assert "transition_error" in entries[0].payload
        assert "conflict:resolved" in entries[0].payload["transition_error"]

    async def test_terminal_failed_cannot_be_resurrected_by_running_event(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-077 regression: FAILED -> RUNNING must be scoped to retry only."""
        task = Task(
            id="task-terminal-failed",
            project_id="proj-1",
            state=TaskState.FAILED,
            proposed_by="agent-1",
            execution_attempts=2,
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("stream:committed", task.id)
        with pytest.raises(ValueError, match="Event rejected"):
            await EventBridge.handle(db_session, event)

        assert task.state == TaskState.FAILED
        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert all(e.event_type == "macro_agent_stream:committed" for e in entries)
        assert any(
            "Invalid transition" in (e.payload.get("transition_error") or "")
            for e in entries
        )

    async def test_landing_completed_passing_completion_contract_moves_to_human_review(
        self,
        db_session: AsyncSession,
    ) -> None:
        task = Task(
            id="task-passing-contract",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            task_contract_json=TaskContract(
                task_id="task-passing-contract",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Exercise success path through EventBridge",
                acceptance=["Task ends in HUMAN_REVIEW when required check passes"],
                completion_contract=CompletionContract(
                    task_id="task-passing-contract",
                    required=[
                        Check(
                            type="always_pass",
                            command="exit 0",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)

        assert task.state == TaskState.HUMAN_REVIEW

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()
        assert any(e.event_type == "verification_passed" for e in entries)
        verification_passed = next(
            e for e in entries if e.event_type == "verification_passed"
        )
        assert verification_passed.actor == "system"
        assert verification_passed.source == "verification_service"
        checks = {c["name"]: c for c in verification_passed.payload["checks"]}
        assert checks["required:always_pass"]["status"] == "passed"

    async def test_agent_review_transition_committed_before_verify(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-095 regression: row lock is released before verification runs."""
        task = Task(
            id="task-gap095-commit",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            task_contract_json=TaskContract(
                task_id="task-gap095-commit",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Verify commit happens before verification",
                acceptance=["Commit count proves lock release"],
                completion_contract=CompletionContract(
                    task_id="task-gap095-commit",
                    required=[
                        Check(
                            type="always_pass",
                            command="exit 0",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        counting_session = _CommitCountingAsyncSession(db_session)

        # Patch _run_check to capture commits that happened before it runs.
        original_run_check = VerificationService._run_check
        commits_before_run_check = 0

        async def _counting_run_check(
            check: Check, timeout: float | None = None, cwd: str | None = None
        ) -> dict[str, object]:
            nonlocal commits_before_run_check
            commits_before_run_check = counting_session.commit_count
            return await original_run_check(check, timeout=timeout, cwd=cwd)

        with patch.object(
            VerificationService, "_run_check", staticmethod(_counting_run_check)
        ):
            event = _make_event("landing:completed", task.id)
            await EventBridge.handle(counting_session, event)

        assert task.state == TaskState.HUMAN_REVIEW
        assert commits_before_run_check >= 1, (
            "AGENT_REVIEW transition should be committed before verification"
        )

    async def test_already_agent_review_still_commits_before_verify(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GAP-095 regression: no transition still commits before verification."""
        task = Task(
            id="task-gap095-already-review",
            project_id="proj-1",
            state=TaskState.AGENT_REVIEW,
            proposed_by="agent-1",
            task_contract_json=TaskContract(
                task_id="task-gap095-already-review",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Verify commit happens before verification",
                acceptance=["Commit count proves lock release"],
                completion_contract=CompletionContract(
                    task_id="task-gap095-already-review",
                    required=[
                        Check(
                            type="always_pass",
                            command="exit 0",
                            expect_exit=0,
                        ),
                    ],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        counting_session = _CommitCountingAsyncSession(db_session)
        original_run_check = VerificationService._run_check
        commits_before_run_check = 0

        async def _counting_run_check(
            check: Check, timeout: float | None = None, cwd: str | None = None
        ) -> dict[str, object]:
            nonlocal commits_before_run_check
            commits_before_run_check = counting_session.commit_count
            return await original_run_check(check, timeout=timeout, cwd=cwd)

        with patch.object(
            VerificationService, "_run_check", staticmethod(_counting_run_check)
        ):
            event = _make_event("landing:completed", task.id)
            await EventBridge.handle(counting_session, event)

        assert task.state == TaskState.HUMAN_REVIEW
        assert commits_before_run_check >= 1, (
            "Pending work should be committed before verification even when "
            "task was already in AGENT_REVIEW"
        )

    async def test_missing_worktree_path_falls_back_to_controller_cwd(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#118: a guessed worktree path that does not exist falls back."""
        from governance_controller.models.processed_event import ProcessedEvent
        from governance_controller.models.project_profile import (
            ProjectProfileModel,
        )
        from governance_controller.schemas.project_profile import ProjectProfile

        task = Task(
            id="task-fallback-worktree",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            execution_attempts=0,
            task_contract_json=TaskContract(
                task_id="task-fallback-worktree",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Verify missing worktree falls back safely",
                acceptance=["Task reaches HUMAN_REVIEW without crashing"],
                completion_contract=CompletionContract(
                    task_id="task-fallback-worktree",
                    required=[Check(type="true", command="true")],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        # Persist a profile whose repository.path has no worktrees/task-id dir.
        profile = ProjectProfile(
            project_id="proj-1",
            project_name="No Worktree",
            repository={"path": "/nonexistent/repository/path"},
            execution={"allowed_harnesses": ["opencode"]},
            security={"forbidden_paths": []},
        )
        db_session.add(
            ProjectProfileModel(
                project_id="proj-1",
                profile_json=profile.model_dump(mode="json"),
            )
        )
        await db_session.flush()

        event = _make_event("landing:completed", task.id)
        await EventBridge.handle(db_session, event)

        # Verification falls back to Controller cwd and succeeds.
        assert task.state == TaskState.HUMAN_REVIEW
        processed = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.task_id == task.id)
        )
        assert processed.scalar_one_or_none() is not None

    async def test_verify_exception_before_state_change_does_not_dedup(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#118: dedup is not recorded if verification raises before transition."""
        from unittest.mock import AsyncMock

        from governance_controller.models.processed_event import ProcessedEvent

        task = Task(
            id="task-verify-raise",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
            execution_attempts=0,
            task_contract_json=TaskContract(
                task_id="task-verify-raise",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Verify exception handling does not dedup",
                acceptance=["Redelivery must be possible"],
                completion_contract=CompletionContract(
                    task_id="task-verify-raise",
                    required=[Check(type="true", command="true")],
                    forbidden_path_check=ForbiddenPathCheck(paths=[]),
                    scope_check=ScopeCheck(
                        description="No scope constraints",
                        allowed_paths=[],
                        forbidden_paths=[],
                    ),
                ),
            ).model_dump(mode="json"),
        )
        db_session.add(task)
        await db_session.flush()

        verifier = VerificationService()
        verifier.verify_and_advance = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("verification exploded before transition")
        )

        event = _make_event("landing:completed", task.id)
        with pytest.raises(RuntimeError, match="verification exploded"):
            await EventBridge.handle(db_session, event, verification_service=verifier)

        # Task never left AGENT_REVIEW and no dedup key was recorded.
        assert task.state == TaskState.AGENT_REVIEW
        processed = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.task_id == task.id)
        )
        assert processed.scalar_one_or_none() is None


class TestEventBridgeCASFailureAudit:
    """#269: a lost outer CAS in handle() must leave a durable audit trail."""

    async def test_lost_cas_writes_concurrent_modification_audit(
        self,
        isolated_db: tuple,
    ) -> None:
        """A genuinely concurrent transition that lands first must not leave
        the loser's rejected attempt with zero audit trace.

        Session B commits RUNNING -> BLOCKED for real via a distinct event
        (``conflict:created``) first. Session A, which loaded the task before
        B's commit, then attempts ``stream:abandoned`` (RUNNING -> FAILED):
        its CAS genuinely fails against the now-BLOCKED/bumped-version row.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-cas-audit-269",
                project_id="proj-1",
                state=TaskState.RUNNING,
                proposed_by="agent-1",
            )
            seed.add(task)
            await seed.commit()

        session_a = local_session()
        task_a = await session_a.scalar(
            select(Task).where(Task.id == "task-cas-audit-269")
        )
        assert task_a is not None

        async with local_session() as session_b:
            task_b = await session_b.scalar(
                select(Task).where(Task.id == "task-cas-audit-269")
            )
            assert task_b is not None
            await EventBridge.handle(
                session_b,
                _make_event("conflict:created", task_b.id, event_id="evt-b-269"),
            )
            await session_b.commit()

        try:
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await EventBridge.handle(
                    session_a,
                    _make_event(
                        "stream:abandoned", task_a.id, event_id="evt-a-269"
                    ),
                )
        finally:
            await session_a.close()

        async with local_session() as check:
            task_row = await check.scalar(
                select(Task).where(Task.id == "task-cas-audit-269")
            )
            assert task_row is not None
            assert task_row.state == TaskState.BLOCKED  # the real winner

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-cas-audit-269")
            )
            events = [a.event_type for a in audits.scalars().all()]
            assert "concurrent_modification" in events


class TestEventBridgeConcurrentRedelivery:
    """#271: genuinely concurrent redelivery of the same event."""

    async def test_racing_delivery_does_not_rerun_verification_or_delete_marker(
        self,
        isolated_db: tuple,
    ) -> None:
        """A racer that wins the in-progress marker insert first must make
        the other delivery short-circuit, not re-run verification.

        Racer B's full ``EventBridge.handle()`` call (a separate session) is
        injected to run BEFORE racer A's own marker-insert attempt — i.e.
        exactly the window the original #271 bug exploited: two deliveries
        both reach the marker-insert step for the identical event before
        either one has committed it. Whichever inserts first (B, by
        construction here) must run verification once; the other (A) must
        see ``won_marker=False`` and return without re-verifying or deleting
        B's marker.
        """
        from unittest.mock import patch

        from governance_controller.models.processed_event import ProcessedEvent
        from governance_controller.services.verification_service import (
            VerificationService,
        )

        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-redelivery-271",
                project_id="proj-1",
                state=TaskState.RUNNING,
                proposed_by="agent-1",
                task_contract_json=TaskContract(
                    task_id="task-redelivery-271",
                    project_id="proj-1",
                    proposed_by="agent-1",
                    objective="race redelivery",
                    acceptance=["ok"],
                    completion_contract=CompletionContract(
                        task_id="task-redelivery-271",
                        required=[Check(type="true", command="true")],
                        forbidden_path_check=ForbiddenPathCheck(paths=[]),
                        scope_check=ScopeCheck(
                            description="no constraints",
                            allowed_paths=[],
                            forbidden_paths=[],
                        ),
                    ),
                ).model_dump(mode="json"),
            )
            seed.add(task)
            await seed.commit()

        verify_call_count = 0
        original_verify_and_advance = VerificationService.verify_and_advance
        original_record_processed_event = EventBridge._record_processed_event
        racer_injected = False

        async def _counting_verify_and_advance(cls, db, task, contract, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal verify_call_count
            verify_call_count += 1
            return await original_verify_and_advance(db, task, contract, **kwargs)

        async def _inject_racer_before_first_insert(db, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal racer_injected
            if not racer_injected:
                racer_injected = True
                # Racer B's request arrives and reaches (and wins) the exact
                # same marker-insert step before A's own attempt below runs.
                async with local_session() as racer_session:
                    await EventBridge.handle(
                        racer_session,
                        _make_event(
                            "landing:completed",
                            "task-redelivery-271",
                            event_id="evt-race-271",
                        ),
                    )
                    await racer_session.commit()
            return await original_record_processed_event(db, **kwargs)

        with (
            patch.object(
                VerificationService,
                "verify_and_advance",
                classmethod(_counting_verify_and_advance),
            ),
            patch.object(
                EventBridge,
                "_record_processed_event",
                staticmethod(_inject_racer_before_first_insert),
            ),
        ):
            async with local_session() as session_a:
                task_a = await session_a.scalar(
                    select(Task).where(Task.id == "task-redelivery-271")
                )
                assert task_a is not None
                await EventBridge.handle(
                    session_a,
                    _make_event(
                        "landing:completed",
                        task_a.id,
                        event_id="evt-race-271",
                    ),
                )
                await session_a.commit()

        assert verify_call_count == 1, (
            "verification must run exactly once despite the racing redelivery"
        )

        async with local_session() as check:
            task_row = await check.scalar(
                select(Task).where(Task.id == "task-redelivery-271")
            )
            assert task_row is not None
            assert task_row.state == TaskState.HUMAN_REVIEW

            markers = await check.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.task_id == "task-redelivery-271"
                )
            )
            marker_rows = markers.scalars().all()
            assert len(marker_rows) == 1, (
                "the losing delivery must not have deleted the winner's marker"
            )
