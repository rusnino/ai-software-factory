"""Tests for the macro-agent event bridge listener."""

from unittest.mock import patch

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
        assert all(
            e.event_type == "macro_agent_landing:completed" for e in entries
        )

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
        assert any(
            e.event_type == "verification_failed"
            for e in entries
        )
        verification_failed = next(
            e for e in entries if e.event_type == "verification_failed"
        )
        assert verification_failed.actor == "system"
        assert verification_failed.source == "verification_service"
        checks = {
            c["name"]: c
            for c in verification_failed.payload["checks"]
        }
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
        await EventBridge.handle(
            db_session, event, verification_service=verifier
        )

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
        await EventBridge.handle(
            db_session, event, verification_service=verifier
        )
        assert task.state == TaskState.RUNNING
        assert task.execution_attempts == 1
        mock_executor.start.assert_awaited_once()

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
        assert any(
            e.event_type == "verification_passed"
            for e in entries
        )
        verification_passed = next(
            e for e in entries if e.event_type == "verification_passed"
        )
        assert verification_passed.actor == "system"
        assert verification_passed.source == "verification_service"
        checks = {
            c["name"]: c
            for c in verification_passed.payload["checks"]
        }
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

        async def _counting_run_check(check: Check) -> dict[str, object]:
            nonlocal commits_before_run_check
            commits_before_run_check = counting_session.commit_count
            return await original_run_check(check)

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

        async def _counting_run_check(check: Check) -> dict[str, object]:
            nonlocal commits_before_run_check
            commits_before_run_check = counting_session.commit_count
            return await original_run_check(check)

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

