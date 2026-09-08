"""Tests for the stuck-execution fallback poller (SPEC-05 §5.6)."""

import asyncio
import multiprocessing
import os
import threading
from datetime import UTC, datetime, timedelta
from queue import Empty
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_httpx
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.cancellation_service import (
    CANCELLATION_CLAIM_LEASE,
)
from governance_controller.services.state_machine import StateMachine
from governance_controller.services.stuck_execution_poller import StuckExecutionPoller
from governance_controller.services.task_service import TaskService


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def _run_cancellation_poller_process(
    database_url: str,
    calls: Any,
    cancel_started: Any,
    release_cancel: Any,
    errors: Any,
) -> None:
    """Run one cancellation poll in an independent process for #293."""

    class _BlockingExecutor:
        async def cancel(self, run_id: str) -> dict[str, Any]:
            calls.put(run_id)
            cancel_started.set()
            if not release_cancel.wait(timeout=5):
                raise RuntimeError("test cancellation release timed out")
            return {}

    async def _poll_once() -> None:
        worker_engine = create_async_engine(database_url, echo=False, future=True)
        worker_sessions = async_sessionmaker(
            worker_engine,
            expire_on_commit=False,
        )
        try:
            async with worker_sessions() as db:
                await StuckExecutionPoller(
                    db,
                    executor=_BlockingExecutor(),  # type: ignore[arg-type]
                )._poll_pending_cancellations()
        finally:
            await worker_engine.dispose()

    try:
        asyncio.run(_poll_once())
    except BaseException as exc:
        errors.put(f"{type(exc).__name__}: {exc}")


async def _running_task_with_execution(
    db_session: Any,
    started_at: datetime,
    timeout_minutes: int = 60,
    macro_agent_run_id: str | None = None,
) -> tuple[Task, Execution]:
    internal_id = str(uuid4())
    external_run_id = macro_agent_run_id or f"run-{internal_id[:8]}"
    task = Task(
        id=f"task-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.RUNNING,
        # Store the external macro-agent run id, matching ApprovalService wiring.
        latest_macro_agent_run_id=external_run_id,
        task_contract_json={
            "execution": {"timeout_minutes": timeout_minutes},
        },
    )
    execution = Execution(
        id=internal_id,
        task_id=task.id,
        state=TaskState.RUNNING,
        started_at=started_at,
        macro_agent_run_id=external_run_id,
    )
    db_session.add(task)
    db_session.add(execution)
    await db_session.flush()
    return task, execution


async def _fetch_task_state(db_session: AsyncSession, task_id: str) -> TaskState:
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    return task.state


class TestStuckExecutionPoller:
    async def test_recent_execution_is_not_blocked(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert actions == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING

    async def test_dry_run_does_not_record_status_error(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry-run status checks must not dirty the execution row."""
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-dry-status-error",
        )

        actions = await StuckExecutionPoller(
            db_session,
            client=_FailingMacroAgentClient(),
            dry_run=True,
        ).poll()

        assert actions[0]["action"] == "would_block"
        assert execution.status_error is None
        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error is None
        assert task.state == TaskState.RUNNING

    async def test_dry_run_preserves_existing_status_error(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#307: inspection must not overwrite an existing status diagnostic."""
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-dry-existing-status-error",
        )
        execution.status_error = "previous-diagnostic"
        await db_session.commit()

        actions = await StuckExecutionPoller(
            db_session,
            client=_FailingMacroAgentClient(),
            dry_run=True,
        ).poll()

        assert actions[0]["action"] == "would_block"
        assert execution.status_error == "previous-diagnostic"
        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error == "previous-diagnostic"
        assert task.state == TaskState.RUNNING

    async def test_stuck_execution_transitions_to_blocked(
        self,
        db_session: Any,
    ) -> None:
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-stuck",
        )

        poller = StuckExecutionPoller(
            db_session,
            client=_FakeMacroAgentClient(),
        )
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        assert actions[0]["task_id"] == task.id
        assert actions[0]["execution_id"] == execution.id
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

    async def test_still_active_execution_stays_running(
        self,
        db_session: Any,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-active",
        )

        client = _FakeMacroAgentClient({"run-active": "running"})
        poller = StuckExecutionPoller(db_session, client=client)
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "alive"
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING

    async def test_status_exception_treats_as_not_alive(
        self,
        db_session: Any,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-error",
        )

        client = _FailingMacroAgentClient()
        poller = StuckExecutionPoller(db_session, client=client)
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == _execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error == "RuntimeError"

    async def test_status_probe_returns_diagnostic_without_mutating_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#329: status diagnostics wait for the winning task transition."""
        _task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-diagnostic-329",
        )

        alive, status_error = await StuckExecutionPoller(
            db_session,
            client=_FailingMacroAgentClient(),
        )._execution_is_still_alive(execution)

        assert alive is False
        assert status_error == "RuntimeError"
        assert execution.status_error is None

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_status_error_is_not_persisted_when_blocked_cas_loses(
        self,
        isolated_db: tuple,
    ) -> None:
        """#329: a losing timeout recovery cannot persist its status diagnostic."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            task, execution = await _running_task_with_execution(
                seed,
                started_at=datetime.now(UTC) - timedelta(minutes=300),
                timeout_minutes=1,
                macro_agent_run_id="run-status-race-329",
            )
            await seed.commit()

        class _RacingFailingClient:
            async def status(self, _run_id: str) -> dict[str, Any]:
                async with local_session() as racer:
                    result = await racer.execute(
                        update(Task)
                        .where(Task.id == task.id)
                        .values(
                            state=TaskState.FAILED.value,
                            version=Task.version + 1,
                        )
                    )
                    assert result.rowcount == 1  # type: ignore[attr-defined]
                    await racer.commit()
                raise RuntimeError("macro-agent unreachable")

        async with local_session() as db:
            actions = await StuckExecutionPoller(
                db,
                client=_RacingFailingClient(),  # type: ignore[arg-type]
            ).poll()

        assert actions == []

        async with local_session() as check:
            task_row = await check.scalar(select(Task).where(Task.id == task.id))
            assert task_row is not None
            assert task_row.state == TaskState.FAILED
            assert task_row.version == 1

            execution_row = await check.scalar(
                select(Execution).where(Execution.id == execution.id)
            )
            assert execution_row is not None
            assert execution_row.state == TaskState.RUNNING
            assert execution_row.ended_at is None
            assert execution_row.status_error is None

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task.id))
            ).scalars().all()
            assert not any(
                row.event_type == "execution_blocked_timeout" for row in audits
            )

    @pytest.mark.parametrize("status_code", [404, 500])
    async def test_status_http_error_persists_status_code(
        self,
        db_session: AsyncSession,
        httpx_mock: pytest_httpx.HTTPXMock,
        status_code: int,
    ) -> None:
        """#286: 404 and 500 status failures remain distinguishable."""
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id=f"run-http-{status_code}",
        )
        httpx_mock.add_response(status_code=status_code)

        poller = StuckExecutionPoller(
            db_session,
            client=MacroAgentClient(base_url="https://example.com"),
        )
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error == f"HTTPStatusError:{status_code}"

    async def test_poller_ignores_stale_latest_run_id_during_retry_window(
        self,
        db_session: Any,
    ) -> None:
        """#237: a stale latest_macro_agent_run_id pointing at an old FAILED
        execution must not cause the poller to block a task whose real, current
        retry execution is still RUNNING without an external run id yet.
        """
        internal_id = str(uuid4())
        task = Task(
            id=f"task-retry-race-{internal_id[:8]}",
            project_id="proj-1",
            proposed_by="agent-1",
            state=TaskState.RUNNING,
            latest_macro_agent_run_id="old-run-id",
            task_contract_json={"execution": {"timeout_minutes": 60}},
        )
        old_execution = Execution(
            id=str(uuid4()),
            task_id=task.id,
            state=TaskState.FAILED,
            started_at=datetime.now(UTC) - timedelta(hours=5),
            ended_at=datetime.now(UTC) - timedelta(hours=4),
            macro_agent_run_id="old-run-id",
        )
        new_execution = Execution(
            id=internal_id,
            task_id=task.id,
            state=TaskState.RUNNING,
            started_at=datetime.now(UTC),
            macro_agent_run_id=None,
        )
        db_session.add(task)
        db_session.add(old_execution)
        db_session.add(new_execution)
        await db_session.flush()

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert actions == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="retry-start recovery requires a real PostgreSQL database",
    )
    async def test_retry_start_sentinel_is_selected_by_poller(
        self,
        isolated_db: tuple,
    ) -> None:
        """#289: a crashed retry start must not strand a RUNNING task."""
        _engine, local_session = isolated_db
        execution_id = str(uuid4())
        task_id = f"task-retry-start-{execution_id[:8]}"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                    latest_macro_agent_run_id=execution_id,
                    task_contract_json={"execution": {"timeout_minutes": 1}},
                )
            )
            seed.add(
                Execution(
                    id=execution_id,
                    task_id=task_id,
                    state=TaskState.RUNNING,
                    started_at=datetime.now(UTC) - timedelta(minutes=10),
                    macro_agent_run_id=None,
                )
            )
            await seed.commit()

        class _UnexpectedStatusClient:
            async def status(self, _run_id: str) -> dict[str, Any]:
                raise AssertionError("retry-start sentinel must never be polled")

        async with local_session() as db:
            actions = await StuckExecutionPoller(
                db,
                client=_UnexpectedStatusClient(),  # type: ignore[arg-type]
            ).poll()

        retry_actions = [
            action for action in actions if action["action"] == "failed_retry_start"
        ]
        assert len(retry_actions) == 1
        assert retry_actions[0]["task_id"] == task_id

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED

            execution = await check.scalar(
                select(Execution).where(Execution.id == execution_id)
            )
            assert execution is not None
            assert execution.state == TaskState.FAILED
            assert execution.ended_at is not None

            audit_rows = await check.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
            assert any(
                row.event_type == "execution_start_failed"
                for row in audit_rows.scalars().all()
            )


class TestPendingRecovery:
    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="requires a real PostgreSQL database for identity-map staleness",
    )
    async def test_retry_start_poll_refreshes_execution_attempts_before_marker_lookup(
        self,
        isolated_db: tuple,
    ) -> None:
        """#338: retry recovery must use the current attempt number."""
        _engine, local_session = isolated_db
        task_id = "task-retry-start-fresh-attempt-338"
        execution_id = "execution-retry-start-fresh-attempt-338"
        marker_id = "event-retry-start-fresh-attempt-338"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                    execution_attempts=1,
                    latest_macro_agent_run_id=execution_id,
                    task_contract_json={"execution": {"timeout_minutes": 1}},
                )
            )
            seed.add(
                Execution(
                    id=execution_id,
                    task_id=task_id,
                    state=TaskState.RUNNING,
                    started_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            seed.add(
                AuditLog(
                    event_id=marker_id,
                    event_type="verification_retry_pending",
                    task_id=task_id,
                    actor="system",
                    source="verification_service",
                    payload={"attempt": 2},
                )
            )
            await seed.commit()

        async with local_session() as db:
            stale_task = await db.scalar(
                select(Task)
                .where(Task.id == task_id)
                .execution_options(populate_existing=True)
            )
            assert stale_task is not None
            assert stale_task.execution_attempts == 1
            await db.commit()

            async with local_session() as writer:
                await writer.execute(
                    update(Task)
                    .where(Task.id == task_id)  # type: ignore[arg-type]
                    .values(execution_attempts=2)
                )
                await writer.commit()

            actions = await StuckExecutionPoller(db)._poll_retry_start()

            assert actions[0]["action"] == "failed_retry_start"
            failure = await db.scalar(
                select(AuditLog)
                .where(
                    AuditLog.task_id == task_id,
                    AuditLog.event_type == "execution_start_failed",
                )
                .order_by(AuditLog.__table__.c.id.desc())  # type: ignore[attr-defined]
            )
            assert failure is not None
            assert failure.payload["pending_event_id"] == marker_id
            assert failure.payload["attempt"] == 2

    @pytest.mark.skipif(
        _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="targets SQLite writer-lock behavior",
    )
    async def test_pending_cancellation_releases_sqlite_writer_lock_before_cancel(
        self,
        isolated_db: tuple,
    ) -> None:
        """#293: an external cancel must not hold SQLite's writer lock."""
        engine, local_session = isolated_db
        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)

        cancel_started = asyncio.Event()
        release_cancel = asyncio.Event()

        async def _cancel(_run_id: str) -> dict[str, object]:
            cancel_started.set()
            await release_cancel.wait()
            return {}

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.side_effect = _cancel
        writer_engine = create_async_engine(
            engine.url,
            connect_args={"timeout": 0.2},
        )
        poller_operation: asyncio.Task[list[dict[str, Any]]] | None = None
        try:
            async with local_session() as poller_db:
                poller_operation = asyncio.create_task(
                    StuckExecutionPoller(
                        poller_db,
                        executor=executor,
                    )._poll_pending_cancellations()
                )
                await asyncio.wait_for(cancel_started.wait(), timeout=5)

                async with AsyncSession(writer_engine) as writer:
                    await writer.execute(
                        update(Task)
                        .where(Task.id == task.id)  # type: ignore[arg-type]
                        .values(version=Task.version + 1)
                    )
                    await asyncio.wait_for(writer.commit(), timeout=1)

                release_cancel.set()
                await asyncio.wait_for(poller_operation, timeout=5)
        finally:
            release_cancel.set()
            if poller_operation is not None and not poller_operation.done():
                await asyncio.gather(poller_operation, return_exceptions=True)
            await writer_engine.dispose()

        executor.cancel.assert_awaited_once_with(run_id)

    async def test_terminal_task_with_matching_pointer_still_cancels_run(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A terminal task pointer is stale ownership, not a live attachment."""
        task_id = "task-terminal-pointer-poller"
        execution_id = "execution-terminal-pointer-poller"
        run_id = "run-terminal-pointer-poller"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.FAILED,
                latest_macro_agent_run_id=run_id,
            )
        )
        db_session.add(
            Execution(
                id=execution_id,
                task_id=task_id,
                state=TaskState.FAILED,
                started_at=datetime.now(UTC) - timedelta(hours=1),
                macro_agent_run_id=run_id,
                cancellation_pending=True,
            )
        )
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.return_value = {}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        )._poll_pending_cancellations()

        executor.cancel.assert_awaited_once_with(run_id)
        assert actions[0]["action"] == "execution_cancel_completed"
        execution = await db_session.scalar(
            select(Execution).where(Execution.id == execution_id)
        )
        assert execution is not None
        assert execution.cancellation_pending is False

    async def test_dry_run_does_not_retry_pending_cancellation(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry-run polling must not call the external cancellation endpoint."""
        task, run_id = await _pending_cancel_task(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)

        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            dry_run=True,
        ).poll()

        assert [action["action"] for action in actions] == [
            "would_cancel_pending_execution"
        ]
        executor.cancel.assert_not_awaited()
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.latest_macro_agent_run_id is None
        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.cancellation_pending is True
        audits = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task.id)
            )
        ).scalars().all()
        assert not any(
            row.event_type == "execution_cancel_completed"
            and row.payload.get("macro_agent_run_id") == run_id
            for row in audits
        )

    async def test_pending_cancellation_refreshes_existing_execution(
        self,
        isolated_db: tuple,
    ) -> None:
        """A queued execution must be refreshed before recovery uses its run ID."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            task, old_run_id = await _pending_cancel_task(seed)
            execution = await seed.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert execution is not None
            execution.cancellation_pending = False
            execution.macro_agent_run_id = old_run_id
            await seed.commit()

        async with local_session() as db:
            stale = await db.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert stale is not None
            assert stale.macro_agent_run_id == old_run_id

            async with local_session() as writer:
                await writer.execute(
                    update(Execution)
                    .where(Execution.id == stale.id)
                    .values(
                        macro_agent_run_id="run-refreshed",
                        cancellation_pending=True,
                    )
                )
                await writer.commit()

            actions = await StuckExecutionPoller(
                db,
                executor=AsyncMock(spec=MacroAgentExecutor),
                dry_run=True,
            )._poll_pending_cancellations()

        assert actions == [
            {
                "task_id": task.id,
                "execution_id": stale.id,
                "action": "would_cancel_pending_execution",
                "macro_agent_run_id": "run-refreshed",
            }
        ]

    async def test_dry_run_does_not_resume_pending_approved_start(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dry-run polling must not resume an approved execution handoff."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        task, _contract = await _approved_task_with_pending_start(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)

        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            dry_run=True,
        ).poll()

        assert [action["action"] for action in actions] == [
            "would_start_execution"
        ]
        executor.start.assert_not_awaited()
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.EXEC_APPROVED

    async def test_dry_run_does_not_resume_pending_verification_retry(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry-run polling must not create a verification retry execution."""
        task, _contract = await _failed_task_with_pending_retry(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)

        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            dry_run=True,
        ).poll()

        assert [action["action"] for action in actions] == [
            "would_start_verification_retry"
        ]
        executor.start.assert_not_awaited()
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.FAILED
        assert refreshed.execution_attempts == 0
        executions = (
            await db_session.execute(
                select(Execution).where(Execution.task_id == task.id)
            )
        ).scalars().all()
        assert executions == []

    async def test_dry_run_missing_retry_profile_does_not_write_failure_audit(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry-run must not audit or commit a missing recovery dependency."""
        task, _contract = await _failed_task_with_pending_retry(db_session)
        profile = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == task.project_id
            )
        )
        assert profile is not None
        await db_session.delete(profile)
        await db_session.commit()
        before = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task.id)
            )
        ).scalars().all()

        executor = AsyncMock(spec=MacroAgentExecutor)
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            dry_run=True,
        ).poll()

        assert [action["action"] for action in actions] == [
            "would_start_verification_retry"
        ]
        executor.start.assert_not_awaited()
        after = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task.id)
            )
        ).scalars().all()
        assert len(after) == len(before)

    async def test_missing_retry_profile_does_not_resurrect_failed_task(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A missing profile must be checked before FAILED -> RUNNING CAS."""
        task, _contract = await _failed_task_with_pending_retry(db_session)
        profile = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == task.project_id
            )
        )
        assert profile is not None
        await db_session.delete(profile)
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        actions = await StuckExecutionPoller(db_session, executor=executor).poll()

        assert any(
            action["action"] == "verification_retry_recovery_failed"
            for action in actions
        )
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.FAILED
        assert refreshed.execution_attempts == 0
        executor.start.assert_not_awaited()

    async def test_retry_recovery_failure_remains_pending(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A transient recovery failure must not consume the retry marker."""
        monkeypatch.setattr(
            StuckExecutionPoller,
            "_retry_recovery_backoff",
            timedelta(0),
            raising=False,
        )
        task, _contract = await _failed_task_with_pending_retry(db_session)
        profile = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == task.project_id
            )
        )
        assert profile is not None
        profile_json = profile.profile_json
        await db_session.delete(profile)
        await db_session.commit()

        first_actions = await StuckExecutionPoller(db_session).poll()
        assert any(
            action["action"] == "verification_retry_recovery_failed"
            for action in first_actions
        )
        db_session.add(
            ProjectProfileModel(
                project_id=task.project_id,
                profile_json=profile_json,
            )
        )
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-retry-after-failure"}
        second_actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert any(
            action["action"] == "verification_retry_recovered"
            for action in second_actions
        )
        executor.start.assert_awaited_once()

    async def test_retryable_profile_failure_keeps_marker_recoverable(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#302: a temporary profile failure must not consume the marker."""
        monkeypatch.setattr(
            StuckExecutionPoller,
            "_retry_recovery_backoff",
            timedelta(0),
            raising=False,
        )
        task, _contract = await _failed_task_with_pending_retry(db_session)
        profile = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == task.project_id
            )
        )
        assert profile is not None
        profile_json = profile.profile_json
        await db_session.delete(profile)
        await db_session.commit()

        first_actions = await StuckExecutionPoller(db_session).poll()
        assert any(
            action["action"] == "verification_retry_recovery_failed"
            for action in first_actions
        )

        db_session.add(
            ProjectProfileModel(
                project_id=task.project_id,
                profile_json=profile_json,
            )
        )
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-retry-after-profile-failure"}
        second_actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert any(
            action["action"] == "verification_retry_recovered"
            for action in second_actions
        )
        executor.start.assert_awaited_once()

    async def test_retry_start_failure_does_not_reopen_same_attempt(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An ambiguous external start failure must be terminal for the attempt."""
        monkeypatch.setattr(
            StuckExecutionPoller,
            "_retry_recovery_backoff",
            timedelta(0),
            raising=False,
        )
        task, _contract = await _failed_task_with_pending_retry(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.side_effect = RuntimeError("macro-agent unavailable")

        first_actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert any(
            action["action"] == "verification_retry_recovery_failed"
            for action in first_actions
        )
        failed = await _fetch_task(db_session, task.id)
        assert failed.state == TaskState.FAILED
        assert failed.execution_attempts == 1

        executor.start.side_effect = None
        executor.start.return_value = {"run_id": "must-not-start"}
        second_actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert not any(
            action["action"] == "verification_retry_recovered"
            for action in second_actions
        )
        executor.start.assert_awaited_once()

    async def test_dry_run_malformed_retry_contract_does_not_write_audit(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Dry-run must remain observational even for invalid persisted data."""
        internal_id = str(uuid4())
        task_id = f"task-dry-malformed-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-dry-malformed",
                proposed_by="agent-1",
                state=TaskState.FAILED,
                task_contract_json={"execution": "not-a-mapping"},
            )
        )
        db_session.add(
            AuditLog(
                event_id=f"evt-dry-malformed-{internal_id[:8]}",
                event_type="verification_retry_pending",
                task_id=task_id,
                actor="system",
                source="verification_service",
                timestamp=datetime.now(UTC) - timedelta(hours=2),
                payload={"attempt": 1, "verification_report": {"passed": False}},
            )
        )
        await db_session.commit()
        before = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
        ).scalars().all()
        executor = AsyncMock(spec=MacroAgentExecutor)

        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            dry_run=True,
        ).poll()

        assert [action["action"] for action in actions] == [
            "would_skip_malformed_retry_contract"
        ]
        after = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
        ).scalars().all()
        assert len(after) == len(before)
        executor.start.assert_not_awaited()

    async def test_completed_retry_marker_does_not_starve_newer_recovery(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A completed old marker must not consume the recovery batch."""
        completed_task, _contract = await _failed_task_with_pending_retry(db_session)
        marker = await db_session.scalar(
            select(AuditLog).where(
                AuditLog.task_id == completed_task.id,
                AuditLog.event_type == "verification_retry_pending",
            )
        )
        assert marker is not None
        db_session.add(
            AuditLog(
                event_id=f"evt-retry-completed-{uuid4()}",
                event_type="verification_retry_recovered",
                task_id=completed_task.id,
                actor="system",
                source="stuck_execution_poller",
                payload={"pending_event_id": marker.event_id, "attempt": 1},
            )
        )
        newer_task, _contract = await _failed_task_with_pending_retry(
            db_session,
            project_id="proj-newer-retry",
        )
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-newer-retry"}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            batch_size=1,
        ).poll()

        recovered = [
            action
            for action in actions
            if action["action"] == "verification_retry_recovered"
        ]
        assert len(recovered) == 1
        assert recovered[0]["task_id"] == newer_task.id
        executor.start.assert_awaited_once()

    async def test_malformed_retry_contract_does_not_abort_other_recovery(
        self,
        db_session: AsyncSession,
    ) -> None:
        """One invalid persisted contract must not stop the poller batch."""
        internal_id = str(uuid4())
        malformed_task_id = f"task-malformed-retry-{internal_id[:8]}"
        db_session.add(
            Task(
                id=malformed_task_id,
                project_id="proj-malformed-retry",
                proposed_by="agent-1",
                state=TaskState.FAILED,
                task_contract_json={"execution": "not-a-mapping"},
            )
        )
        db_session.add(
            AuditLog(
                event_id=f"evt-malformed-retry-{internal_id[:8]}",
                event_type="verification_retry_pending",
                task_id=malformed_task_id,
                actor="system",
                source="verification_service",
                timestamp=datetime.now(UTC) - timedelta(hours=2),
                payload={"attempt": 1, "verification_report": {"passed": False}},
            )
        )
        valid_task, _contract = await _failed_task_with_pending_retry(db_session)

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-valid-after-malformed"}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert any(
            action["action"] == "verification_retry_recovery_failed"
            and action["task_id"] == malformed_task_id
            for action in actions
        )
        failure_audit = await db_session.scalar(
            select(AuditLog)
            .where(
                AuditLog.task_id == malformed_task_id,
                AuditLog.event_type == "verification_retry_recovery_failed",
            )
            .order_by(AuditLog.__table__.c.id.desc())  # type: ignore[attr-defined]
        )
        assert failure_audit is not None
        assert failure_audit.payload["retryable"] is False
        assert any(
            action["action"] == "verification_retry_recovered"
            and action["task_id"] == valid_task.id
            for action in actions
        )
        malformed = await _fetch_task(db_session, malformed_task_id)
        assert malformed.state == TaskState.FAILED
        executor.start.assert_awaited_once()

    async def test_profile_lookup_failure_is_not_recorded_as_recovery_failure(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Operational profile-store failures must remain visible to the caller."""
        task, _contract = await _failed_task_with_pending_retry(db_session)

        async def fail_profile_lookup(
            _service: TaskService,
            _project_id: str,
        ) -> None:
            raise RuntimeError("profile store unavailable")

        monkeypatch.setattr(
            TaskService,
            "get_profile_by_project_id",
            fail_profile_lookup,
        )

        with pytest.raises(RuntimeError, match="profile store unavailable"):
            await StuckExecutionPoller(db_session).poll()

        failure_audit = await db_session.scalar(
            select(AuditLog).where(
                AuditLog.task_id == task.id,
                AuditLog.event_type == "verification_retry_recovery_failed",
            )
        )
        assert failure_audit is None

    async def test_malformed_retry_profile_does_not_abort_other_recovery(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#306: one invalid profile must not stop a later valid recovery."""
        internal_id = str(uuid4())
        malformed_task_id = f"task-malformed-profile-{internal_id[:8]}"
        malformed_project_id = f"proj-malformed-profile-{internal_id[:8]}"
        contract = TaskContract(
            task_id=malformed_task_id,
            project_id=malformed_project_id,
            proposed_by="agent-1",
            objective="Recover despite malformed profile",
            acceptance=["the later retry still starts"],
            execution={"timeout_minutes": 1, "max_retries": 2},
        )
        db_session.add(
            Task(
                id=malformed_task_id,
                project_id=malformed_project_id,
                proposed_by="agent-1",
                state=TaskState.FAILED,
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        db_session.add(
            ProjectProfileModel(
                project_id=malformed_project_id,
                profile_json={"not_a_profile": True},
            )
        )
        db_session.add(
            AuditLog(
                event_id=f"evt-malformed-profile-{internal_id[:8]}",
                event_type="verification_retry_pending",
                task_id=malformed_task_id,
                actor="system",
                source="verification_service",
                timestamp=datetime.now(UTC) - timedelta(hours=3),
                payload={"attempt": 1, "verification_report": {"passed": False}},
            )
        )
        valid_task, _contract = await _failed_task_with_pending_retry(db_session)

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-valid-after-malformed-profile"}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert any(
            action["action"] == "verification_retry_recovery_failed"
            and action["task_id"] == malformed_task_id
            for action in actions
        )
        failure_audit = await db_session.scalar(
            select(AuditLog)
            .where(
                AuditLog.task_id == malformed_task_id,
                AuditLog.event_type == "verification_retry_recovery_failed",
            )
            .order_by(AuditLog.__table__.c.id.desc())  # type: ignore[attr-defined]
        )
        assert failure_audit is not None
        assert failure_audit.payload["retryable"] is False
        assert any(
            action["action"] == "verification_retry_recovered"
            and action["task_id"] == valid_task.id
            for action in actions
        )
        executor.start.assert_awaited_once()

        second_actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()
        assert not any(
            action["task_id"] == malformed_task_id
            for action in second_actions
        )
        executor.start.assert_awaited_once()

    async def test_stale_approved_start_marker_restarts_execution(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An interrupted approval handoff is resumed by the poller."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        task, _contract = await _approved_task_with_pending_start(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-approved-recovery"}

        actions = await StuckExecutionPoller(db_session, executor=executor).poll()

        assert any(
            action["action"] == "execution_start_recovered" for action in actions
        )
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.RUNNING
        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-approved-recovery"
        executor.start.assert_awaited_once()

    async def test_pending_cancellation_is_retried_and_completed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """The poller retries an external cancellation after a prior failure."""
        _task, run_id = await _pending_cancel_task(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.return_value = {}

        actions = await StuckExecutionPoller(db_session, executor=executor).poll()

        assert any(
            action["action"] == "execution_cancel_completed" for action in actions
        )
        executor.cancel.assert_awaited_once_with(run_id)
        audit_rows = (
            await db_session.execute(select(AuditLog))
        ).scalars().all()
        assert any(row.event_type == "execution_cancel_completed" for row in audit_rows)

    async def test_failed_cancellation_stays_queued_for_retry(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A failed external cancel keeps the durable queue row pending."""
        task, run_id = await _pending_cancel_task(db_session)
        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.side_effect = [
            RuntimeError("macro-agent unavailable"),
            {},
        ]
        poller = StuckExecutionPoller(db_session, executor=executor)

        first_actions = await poller._poll_pending_cancellations()
        assert first_actions == [
            {
                "task_id": task.id,
                "execution_id": execution.id,
                "action": "execution_cancel_failed",
                "macro_agent_run_id": run_id,
            }
        ]
        assert execution.cancellation_pending is True

        second_actions = await poller._poll_pending_cancellations()
        assert second_actions == [
            {
                "task_id": task.id,
                "execution_id": execution.id,
                "action": "execution_cancel_completed",
                "macro_agent_run_id": run_id,
            }
        ]
        assert execution.cancellation_pending is False
        assert executor.cancel.await_count == 2

    async def test_missing_cancel_run_is_treated_as_completed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A missing external run is already in the desired cancelled state."""
        task, run_id = await _pending_cancel_task(db_session)
        request = httpx.Request("POST", f"https://macro.example/runs/{run_id}/cancel")
        response = httpx.Response(404, request=request)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.side_effect = httpx.HTTPStatusError(
            "run not found",
            request=request,
            response=response,
        )

        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
        ).poll()

        assert [action["action"] for action in actions] == [
            "execution_cancel_completed"
        ]
        audit_rows = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task.id)
            )
        ).scalars().all()
        completed = [
            row for row in audit_rows if row.event_type == "execution_cancel_completed"
        ]
        assert len(completed) == 1
        assert completed[0].payload["reason"] == "run_not_found"

        await StuckExecutionPoller(db_session, executor=executor).poll()
        executor.cancel.assert_awaited_once_with(run_id)

    async def test_pending_cancellation_batch_excludes_completed_history(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Cancellation recovery honors batch_size without scanning old outcomes."""
        _completed_task, completed_run_id = await _pending_cancel_task(db_session)
        completed_task = await _fetch_task(db_session, _completed_task.id)
        completed_execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == completed_task.id)
        )
        assert completed_execution is not None
        completed_execution.cancellation_pending = False
        db_session.add(
            AuditLog(
                event_id=f"evt-cancel-completed-{uuid4()}",
                event_type="execution_cancel_completed",
                task_id=completed_task.id,
                execution_id=completed_execution.id,
                actor="system",
                source="stuck_execution_poller",
                payload={"macro_agent_run_id": completed_run_id},
            )
        )
        _pending_task, pending_run_id = await _pending_cancel_task(db_session)
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.return_value = {}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            batch_size=1,
        ).poll()

        assert [action["macro_agent_run_id"] for action in actions] == [
            pending_run_id
        ]
        executor.cancel.assert_awaited_once_with(pending_run_id)

    async def test_pending_cancellation_poll_respects_batch_size(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Cancellation recovery returns no more than ``batch_size`` queue rows."""
        # Build a large tail of completed cancellation history.
        completed_tasks: list[Task] = []
        for _ in range(50):
            task, run_id = await _pending_cancel_task(db_session)
            completed_tasks.append(task)
            execution = await db_session.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert execution is not None
            execution.cancellation_pending = False
            await AuditService.log(
                db=db_session,
                event_type="execution_cancel_completed",
                task_id=task.id,
                actor="system",
                source="stuck_execution_poller",
                execution_id=execution.id,
                payload={"macro_agent_run_id": run_id},
            )
            await db_session.commit()

        pending: list[tuple[Task, str]] = []
        for _ in range(3):
            task, run_id = await _pending_cancel_task(db_session)
            pending.append((task, run_id))
        await db_session.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.return_value = {}
        actions = await StuckExecutionPoller(
            db_session,
            executor=executor,
            batch_size=2,
        ).poll()

        assert len(actions) == 2
        assert executor.cancel.await_count == 2
        returned_run_ids = {
            action["macro_agent_run_id"]
            for action in actions
            if action["action"] == "execution_cancel_completed"
        }
        pending_run_ids = {run_id for _, run_id in pending}
        assert len(returned_run_ids) == 2
        assert returned_run_ids.issubset(pending_run_ids)

        # None of the completed-history tasks should be reconsidered.
        completed_ids = {task.id for task in completed_tasks}
        for action in actions:
            assert action["task_id"] not in completed_ids

    async def test_stale_verification_retry_marker_restarts_execution(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An interrupted verification retry is resumed by the poller."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        task, _contract = await _failed_task_with_pending_retry(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-retry-recovery"}

        actions = await StuckExecutionPoller(db_session, executor=executor).poll()

        assert any(
            action["action"] == "verification_retry_recovered" for action in actions
        )
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.RUNNING
        assert refreshed.execution_attempts == 1
        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-retry-recovery"
        executor.start.assert_awaited_once()


async def _ready_task_with_execution(
    db_session: Any,
    started_at: datetime,
    timeout_minutes: int = 60,
) -> tuple[Task, Execution]:
    internal_id = str(uuid4())
    task = Task(
        id=f"task-ready-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.READY,
        task_contract_json={
            "execution": {"timeout_minutes": timeout_minutes},
        },
    )
    execution = Execution(
        id=internal_id,
        task_id=task.id,
        state=TaskState.READY,
        started_at=started_at,
        macro_agent_run_id=None,
    )
    db_session.add(task)
    db_session.add(execution)
    await db_session.flush()
    return task, execution


async def _agent_review_task_with_marker(
    db_session: Any,
    processed_at: datetime,
) -> tuple[Task, ProcessedEvent]:
    internal_id = str(uuid4())
    task = Task(
        id=f"task-review-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.AGENT_REVIEW,
        task_contract_json={"execution": {"timeout_minutes": 60}},
    )
    marker = ProcessedEvent(
        task_id=task.id,
        event_type="landing:completed",
        event_timestamp=datetime.now(UTC),
        event_id=f"evt-{internal_id[:8]}",
        processed_at=processed_at,
    )
    db_session.add(task)
    db_session.add(marker)
    await db_session.flush()
    return task, marker


async def _approved_task_with_pending_start(
    db_session: AsyncSession,
) -> tuple[Task, TaskContract]:
    """Seed an approved task whose execution handoff was interrupted."""
    internal_id = str(uuid4())
    task_id = f"task-approved-recovery-{internal_id[:8]}"
    contract = TaskContract(
        task_id=task_id,
        project_id="proj-approved-recovery",
        proposed_by="agent-1",
        objective="Recover an approved execution",
        acceptance=["the execution starts exactly once"],
        execution={"timeout_minutes": 1, "harness": "opencode", "role": "worker"},
    )
    profile = ProjectProfile(
        project_id=contract.project_id,
        repository={"path": "/tmp/recovery-repo"},
        execution={"allowed_harnesses": ["opencode"]},
    )
    task = Task(
        id=task_id,
        project_id=contract.project_id,
        proposed_by=contract.proposed_by,
        state=TaskState.EXEC_APPROVED,
        task_contract_json=contract.model_dump(mode="json"),
    )
    db_session.add(task)
    db_session.add(
        ProjectProfileModel(
            project_id=profile.project_id,
            profile_json=profile.model_dump(mode="json"),
        )
    )
    db_session.add(
        AuditLog(
            event_id=f"evt-approved-recovery-{internal_id[:8]}",
            event_type="execution_start_pending",
            task_id=task_id,
            actor="system",
            source="approval_service",
            timestamp=datetime.now(UTC) - timedelta(hours=3),
            payload={"operation": "execution_start", "timeout_minutes": 1},
        )
    )
    await db_session.commit()
    return task, contract


async def _pending_cancel_task(
    db_session: AsyncSession,
) -> tuple[Task, str]:
    """Seed a task with an unresolved external-run cancellation marker."""
    internal_id = str(uuid4())
    task_id = f"task-cancel-recovery-{internal_id[:8]}"
    execution_id = f"execution-cancel-recovery-{internal_id[:8]}"
    run_id = f"run-cancel-recovery-{internal_id[:8]}"
    db_session.add(
        Task(
            id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            state=TaskState.FAILED,
            latest_macro_agent_run_id=None,
        )
    )
    db_session.add(
        Execution(
            id=execution_id,
            task_id=task_id,
            state=TaskState.FAILED,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            macro_agent_run_id=run_id,
            cancellation_pending=True,
        )
    )
    db_session.add(
        AuditLog(
            event_id=f"evt-cancel-recovery-{internal_id[:8]}",
            event_type="execution_cancel_pending",
            task_id=task_id,
            execution_id=execution_id,
            actor="system",
            source="approval_service",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
            payload={
                "execution_id": f"execution-{internal_id[:8]}",
                "macro_agent_run_id": run_id,
            },
        )
    )
    await db_session.commit()
    return await _fetch_task(db_session, task_id), run_id


async def _failed_task_with_pending_retry(
    db_session: AsyncSession,
    project_id: str = "proj-retry-recovery",
) -> tuple[Task, TaskContract]:
    """Seed a failed task whose verification retry handoff was interrupted."""
    internal_id = str(uuid4())
    task_id = f"task-retry-recovery-{internal_id[:8]}"
    contract = TaskContract(
        task_id=task_id,
        project_id=project_id,
        proposed_by="agent-1",
        objective="Recover a verification retry",
        acceptance=["the retry execution starts"],
        execution={"timeout_minutes": 1, "max_retries": 2},
    )
    profile = ProjectProfile(
        project_id=contract.project_id,
        repository={"path": "/tmp/recovery-repo"},
        execution={"allowed_harnesses": ["opencode"]},
    )
    task = Task(
        id=task_id,
        project_id=contract.project_id,
        proposed_by=contract.proposed_by,
        state=TaskState.FAILED,
        execution_attempts=0,
        task_contract_json=contract.model_dump(mode="json"),
    )
    db_session.add(task)
    db_session.add(
        ProjectProfileModel(
            project_id=profile.project_id,
            profile_json=profile.model_dump(mode="json"),
        )
    )
    db_session.add(
        AuditLog(
            event_id=f"evt-retry-recovery-{internal_id[:8]}",
            event_type="verification_retry_pending",
            task_id=task_id,
            actor="system",
            source="verification_service",
            timestamp=datetime.now(UTC) - timedelta(hours=2),
            payload={
                "attempt": 1,
                "max_retries": 2,
                "verification_report": {"passed": False},
            },
        )
    )
    await db_session.commit()
    return task, contract


async def _fetch_task(db_session: AsyncSession, task_id: str) -> Task:
    task = await db_session.scalar(select(Task).where(Task.id == task_id))
    assert task is not None
    return task


class TestPollReady:
    """#253: READY tasks whose executor.start() never completed."""

    async def test_execution_start_never_completed_fails_after_deadline(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, execution = await _ready_task_with_execution(
            db_session,
            # timeout_minutes=1, crash factor 2x -> 2 minute deadline.
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            timeout_minutes=1,
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        ready_actions = [a for a in actions if a["action"] == "failed_ready"]
        assert len(ready_actions) == 1
        assert ready_actions[0]["task_id"] == task.id
        assert ready_actions[0]["execution_id"] == execution.id

        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.FAILED

        result = await db_session.execute(
            select(Execution).where(Execution.id == execution.id)
        )
        refreshed_execution = result.scalar_one()
        assert refreshed_execution.state == TaskState.FAILED
        assert refreshed_execution.ended_at is not None

    async def test_execution_within_grace_window_is_not_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, _execution = await _ready_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(seconds=5),
            timeout_minutes=60,
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [a for a in actions if a["action"] == "failed_ready"] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.READY

    async def test_execution_with_run_id_is_not_touched(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A READY execution that already got a real run id is not #253's case."""
        task, execution = await _ready_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            timeout_minutes=1,
        )
        execution.macro_agent_run_id = "run-already-started"
        await db_session.flush()

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [a for a in actions if a["action"] == "failed_ready"] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.READY


class TestPollAgentReview:
    """#254: AGENT_REVIEW tasks abandoned by a crash during verification."""

    async def test_stale_marker_cannot_block_behind_a_fresh_marker(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Only the latest verification marker may drive stale recovery."""
        internal_id = str(uuid4())
        task_id = f"task-review-markers-{internal_id[:8]}"
        now = datetime.now(UTC)
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.AGENT_REVIEW,
            )
        )
        db_session.add(
            ProcessedEvent(
                task_id=task_id,
                event_type="landing:completed",
                event_timestamp=now - timedelta(hours=2),
                event_id=f"evt-old-review-{internal_id[:8]}",
                processed_at=now - timedelta(hours=1),
            )
        )
        db_session.add(
            ProcessedEvent(
                task_id=task_id,
                event_type="landing:completed",
                event_timestamp=now,
                event_id=f"evt-fresh-review-{internal_id[:8]}",
                processed_at=now - timedelta(minutes=1),
            )
        )
        await db_session.commit()

        actions = await StuckExecutionPoller(db_session).poll()

        assert [
            action
            for action in actions
            if action["action"] == "unblocked_agent_review"
        ] == []
        assert await _fetch_task_state(db_session, task_id) == TaskState.AGENT_REVIEW

    async def test_stale_marker_transitions_to_blocked_and_deletes_marker(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, marker = await _agent_review_task_with_marker(
            db_session,
            # Fixed 15-minute verification-crash budget (#264); well past it.
            processed_at=datetime.now(UTC) - timedelta(minutes=30),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        review_actions = [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ]
        assert len(review_actions) == 1
        assert review_actions[0]["task_id"] == task.id

        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

        result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.id == marker.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_marker_within_grace_window_is_not_touched(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, marker = await _agent_review_task_with_marker(
            db_session,
            processed_at=datetime.now(UTC) - timedelta(minutes=2),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.AGENT_REVIEW

        result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.id == marker.id)
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="aiosqlite returns tz-naive datetimes for this fixture's "
        "engine setup, which the poller's tz-aware comparison rejects; "
        "the CAS-loss semantics under test require real Postgres anyway",
    )
    async def test_marker_survives_when_blocked_cas_genuinely_loses(
        self,
        isolated_db: tuple,
    ) -> None:
        """#263: a concurrent winner must not have its dedup marker deleted.

        Genuinely mutates the task's state/version from a SEPARATE session
        between the poller's own SELECT and its ``atomic_transition`` call, so
        the poller's CAS to BLOCKED loses for real (version/state mismatch),
        not via a mocked return value. The marker — which a legitimately
        successful concurrent event delivery may still need — must survive.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            task, marker = await _agent_review_task_with_marker(
                seed, processed_at=datetime.now(UTC) - timedelta(minutes=30)
            )
            task_id = task.id
            marker_id = marker.id
            await seed.commit()

        original_atomic_transition = StateMachine.atomic_transition

        async def _race_then_attempt(db, task, target_state):  # type: ignore[no-untyped-def]
            if target_state == TaskState.BLOCKED:
                async with local_session() as racer:
                    await racer.execute(
                        update(Task)
                        .where(Task.id == task_id)
                        .values(state=TaskState.FAILED.value, version=Task.version + 1)
                    )
                    await racer.commit()
            return await original_atomic_transition(db, task, target_state)

        async with local_session() as poller_session:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(StateMachine, "atomic_transition", _race_then_attempt)
                poller = StuckExecutionPoller(poller_session)
                actions = await poller.poll()

        assert [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ] == []

        async with local_session() as check:
            result = await check.execute(select(Task).where(Task.id == task_id))
            task_row = result.scalar_one()
            assert task_row.state == TaskState.FAILED  # the real winner

            marker_result = await check.execute(
                select(ProcessedEvent).where(ProcessedEvent.id == marker_id)
            )
            assert marker_result.scalar_one_or_none() is not None, (
                "the loser's failed CAS must not delete the marker a "
                "concurrent winner may still depend on"
            )


class _FakeMacroAgentClient:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self._statuses = statuses or {}

    async def status(self, run_id: str) -> dict[str, Any]:
        return {"status": self._statuses.get(run_id, "completed")}


class TestCancellationLargeHistory:
    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="large-history bounded-cost test requires PostgreSQL",
    )
    async def test_pending_cancellation_selector_does_not_scan_resolved_history(
        self,
        isolated_db: tuple,
    ) -> None:
        """#308: polling a live cancellation does not scan audit history."""
        _engine, local_session = isolated_db
        n_history = 50_000
        target_task_id = "task-cancel-live"
        target_execution_id = "execution-cancel-live"
        target_run_id = "run-cancel-live"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=target_task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.FAILED,
                )
            )
            execution_rows = [
                {
                    "id": f"execution-cancel-history-{i}",
                    "task_id": f"task-cancel-history-{i}",
                    "state": TaskState.FAILED.value,
                    "started_at": datetime.now(UTC) - timedelta(days=1),
                    "macro_agent_run_id": f"run-cancel-history-{i}",
                }
                for i in range(n_history)
            ]
            execution_rows.append(
                {
                    "id": target_execution_id,
                    "task_id": target_task_id,
                    "state": TaskState.FAILED.value,
                    "started_at": datetime.now(UTC) - timedelta(hours=2),
                    "macro_agent_run_id": target_run_id,
                }
            )
            if "cancellation_pending" in Execution.__table__.c:  # type: ignore[attr-defined]
                for row in execution_rows:
                    row["cancellation_pending"] = row["id"] == target_execution_id
            await seed.execute(insert(Execution), execution_rows)

            timestamp = datetime.now(UTC) - timedelta(hours=2)
            history_rows: list[dict[str, Any]] = []
            for i in range(n_history):
                task_id = f"task-cancel-history-{i}"
                run_id = f"run-cancel-history-{i}"
                history_rows.extend(
                    [
                        {
                            "event_id": f"evt-cancel-history-pending-{i}",
                            "event_type": "execution_cancel_pending",
                            "task_id": task_id,
                            "execution_id": f"execution-cancel-history-{i}",
                            "actor": "system",
                            "source": "stuck_execution_poller",
                            "timestamp": timestamp,
                            "payload": {"macro_agent_run_id": run_id},
                        },
                        {
                            "event_id": f"evt-cancel-history-completed-{i}",
                            "event_type": "execution_cancel_completed",
                            "task_id": task_id,
                            "execution_id": f"execution-cancel-history-{i}",
                            "actor": "system",
                            "source": "stuck_execution_poller",
                            "timestamp": timestamp,
                            "payload": {"macro_agent_run_id": run_id},
                        },
                    ]
                )
            history_rows.append(
                {
                    "event_id": "evt-cancel-live-pending",
                    "event_type": "execution_cancel_pending",
                    "task_id": target_task_id,
                    "execution_id": target_execution_id,
                    "actor": "system",
                    "source": "stuck_execution_poller",
                    "timestamp": timestamp,
                    "payload": {"macro_agent_run_id": target_run_id},
                }
            )
            await seed.execute(
                insert(AuditLog),
                history_rows,
            )
            await seed.commit()
            await seed.execute(text("ANALYZE auditlog"))
            await seed.execute(text("ANALYZE execution"))
            await seed.commit()

        selector_statements: list[tuple[str, Any]] = []

        def _capture_selector(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if normalized.lstrip().startswith("select") and (
                "from auditlog" in normalized or "from execution" in normalized
            ):
                selector_statements.append((statement, parameters))

        event.listen(_engine.sync_engine, "before_cursor_execute", _capture_selector)
        try:
            async with local_session() as db:
                actions = await StuckExecutionPoller(
                    db, dry_run=True, batch_size=1
                )._poll_pending_cancellations()
        finally:
            event.remove(
                _engine.sync_engine, "before_cursor_execute", _capture_selector
            )

        assert actions == [
            {
                "task_id": target_task_id,
                "execution_id": target_execution_id,
                "action": "would_cancel_pending_execution",
                "macro_agent_run_id": target_run_id,
            }
        ]
        assert selector_statements

        selector, parameters = selector_statements[0]
        async with _engine.connect() as connection:
            explain_result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + selector,
                parameters,
            )
            plan = "\n".join(row[0] for row in explain_result)

        assert "auditlog" not in plan.lower(), plan
        assert "ix_execution_cancellation_pending" in plan, plan


class TestCancellationConcurrency:
    @pytest.mark.skipif(
        _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="targets SQLite cross-event-loop cancellation claims",
    )
    async def test_sqlite_cancellation_claim_is_single_flight_across_event_loops(
        self,
        isolated_db: tuple,
    ) -> None:
        """Two independent loops must not cancel one pending run twice."""
        engine, local_session = isolated_db

        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)

        database_url = engine.url.render_as_string(hide_password=False)
        calls: list[str] = []
        calls_lock = threading.Lock()
        cancel_started = threading.Event()
        release_cancel = threading.Event()
        errors: list[BaseException] = []
        results: list[list[dict[str, Any]]] = []

        class _BlockingExecutor:
            async def cancel(self, current_run_id: str) -> dict[str, Any]:
                with calls_lock:
                    calls.append(current_run_id)
                    is_first_call = len(calls) == 1
                if is_first_call:
                    cancel_started.set()
                    if not release_cancel.wait(timeout=5):
                        raise RuntimeError("test cancellation release timed out")
                return {}

        async def _poll_once() -> list[dict[str, Any]]:
            worker_engine = create_async_engine(database_url, echo=False, future=True)
            worker_sessions = async_sessionmaker(
                worker_engine,
                expire_on_commit=False,
            )
            try:
                async with worker_sessions() as db:
                    return await StuckExecutionPoller(
                        db,
                        executor=_BlockingExecutor(),
                    )._poll_pending_cancellations()
            finally:
                await worker_engine.dispose()

        def _run_in_thread() -> None:
            try:
                results.append(asyncio.run(_poll_once()))
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=_run_in_thread)
        second = threading.Thread(target=_run_in_thread)
        first.start()
        assert await asyncio.to_thread(cancel_started.wait, 5)
        second.start()
        await asyncio.to_thread(second.join, 5)
        assert not second.is_alive()
        release_cancel.set()
        await asyncio.to_thread(first.join, 5)
        assert not first.is_alive()

        assert errors == []
        assert calls == [run_id]
        assert len(results) == 2
        async with local_session() as check:
            completion_audits = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task.id,
                        AuditLog.event_type == "execution_cancel_completed",
                    )
                )
            ).scalars().all()
            assert len(completion_audits) == 1

    @pytest.mark.skipif(
        _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="targets SQLite cross-process cancellation claims",
    )
    async def test_sqlite_cancellation_claim_is_single_flight_across_processes(
        self,
        isolated_db: tuple,
    ) -> None:
        """Independent processes must not cancel one pending run twice."""
        engine, local_session = isolated_db

        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)

        context = multiprocessing.get_context("spawn")
        database_url = engine.url.render_as_string(hide_password=False)
        calls = context.Queue()
        errors = context.Queue()
        cancel_started = context.Event()
        release_cancel = context.Event()
        first = context.Process(
            target=_run_cancellation_poller_process,
            args=(database_url, calls, cancel_started, release_cancel, errors),
        )
        second = context.Process(
            target=_run_cancellation_poller_process,
            args=(database_url, calls, cancel_started, release_cancel, errors),
        )
        first.start()
        try:
            assert await asyncio.to_thread(cancel_started.wait, 5)
            second.start()
            await asyncio.to_thread(second.join, 5)
            assert not second.is_alive()
            assert second.exitcode == 0
            release_cancel.set()
            await asyncio.to_thread(first.join, 5)
            assert not first.is_alive()
            assert first.exitcode == 0

            with pytest.raises(Empty):
                errors.get(timeout=0.2)
            assert calls.get(timeout=5) == run_id
            with pytest.raises(Empty):
                calls.get(timeout=0.2)
        finally:
            release_cancel.set()
            for process in (first, second):
                if process.is_alive():
                    process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            calls.close()
            errors.close()

        async with local_session() as check:
            completion_audits = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task.id,
                        AuditLog.event_type == "execution_cancel_completed",
                    )
                )
            ).scalars().all()
            assert len(completion_audits) == 1


    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="task-pointer revalidation requires PostgreSQL row locking",
    )
    async def test_pending_cancellation_revalidates_locked_task_pointer(
        self,
        isolated_db: tuple,
    ) -> None:
        """#293: a concurrent task attachment wins before cancellation."""
        engine, local_session = isolated_db

        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)

        writer = local_session()
        await writer.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(
                state=TaskState.RUNNING.value,
                latest_macro_agent_run_id=run_id,
            )
        )

        task_query_started = asyncio.Event()
        task_query_read = asyncio.Event()
        query_for_update = False
        lock_order: list[str] = []

        def _capture_task_query(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            nonlocal query_for_update
            normalized = statement.lower()
            if normalized.lstrip().startswith("select") and "from task" in normalized:
                query_for_update = "for update" in normalized
                task_query_started.set()
            if "for update" in normalized:
                if "from execution" in normalized:
                    lock_order.append("execution")
                elif "from task" in normalized:
                    lock_order.append("task")

        event.listen(engine.sync_engine, "before_cursor_execute", _capture_task_query)
        poller_session = local_session()
        executor = AsyncMock(spec=MacroAgentExecutor)
        cancel_release = asyncio.Event()

        async def _cancel(_run_id: str) -> dict[str, Any]:
            await cancel_release.wait()
            return {}

        executor.cancel.side_effect = _cancel
        original_scalar = poller_session.scalar

        async def _scalar(*args: Any, **kwargs: Any) -> Any:
            result = await original_scalar(*args, **kwargs)
            if isinstance(result, Task):
                task_query_read.set()
            return result

        poller_session.scalar = _scalar  # type: ignore[method-assign]
        poller_task = asyncio.create_task(
            StuckExecutionPoller(
                poller_session,
                executor=executor,
            )._poll_pending_cancellations()
        )
        try:
            await asyncio.wait_for(task_query_started.wait(), timeout=5)
            if query_for_update:
                # The locked query is waiting on the writer's uncommitted
                # attachment. Releasing it lets the poller observe the pointer.
                await writer.commit()
            else:
                # The unlocked parent query must finish and capture the old
                # pointer before the concurrent attachment commits.
                await asyncio.wait_for(task_query_read.wait(), timeout=5)
                await writer.commit()
            cancel_release.set()
            actions = await asyncio.wait_for(poller_task, timeout=5)
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", _capture_task_query
            )
            cancel_release.set()
            if not poller_task.done():
                poller_task.cancel()
            await asyncio.gather(poller_task, return_exceptions=True)
            await poller_session.close()
            await writer.close()

        assert len(actions) == 1
        assert actions[0]["task_id"] == task.id
        assert actions[0]["action"] == "execution_cancel_completed"
        executor.cancel.assert_not_awaited()
        assert actions[0]["reason"] == "run_attached_to_current_task"
        assert lock_order[:2] == ["execution", "task"]

        async with local_session() as check:
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert execution is not None
            assert execution.cancellation_pending is False

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="SKIP LOCKED concurrency test requires PostgreSQL",
    )
    async def test_overlapping_pollers_do_not_double_process_cancellations(
        self,
        isolated_db: tuple,
    ) -> None:
        """#313: concurrent poller passes must not select the same marker."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            pending: list[tuple[Task, str]] = []
            for _ in range(4):
                task, run_id = await _pending_cancel_task(seed)
                pending.append((task, run_id))
            await seed.commit()

        async def _poll() -> list[dict[str, Any]]:
            async with local_session() as db:
                executor = AsyncMock(spec=MacroAgentExecutor)

                async def _slow_cancel(_run_id: str) -> dict[str, Any]:
                    await asyncio.sleep(0.1)
                    return {}

                executor.cancel.side_effect = _slow_cancel
                poller = StuckExecutionPoller(db, executor=executor)
                return await poller._poll_pending_cancellations()

        actions_a, actions_b = await asyncio.gather(_poll(), _poll())
        all_actions = actions_a + actions_b
        completed = [
            action
            for action in all_actions
            if action["action"] == "execution_cancel_completed"
        ]
        task_ids = [action["task_id"] for action in completed]
        assert len(completed) == 4
        assert len(task_ids) == len(set(task_ids))

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="deterministic lock interleave requires PostgreSQL",
    )
    async def test_batch_lock_interleave_does_not_duplicate_cancellations(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#323: committing one row must not invalidate a stale batch claim."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            for _ in range(5):
                await _pending_cancel_task(seed)
            await seed.commit()

        first_commit_paused = asyncio.Event()
        release_first_commit = asyncio.Event()
        commit_paused = False
        original_commit = AsyncSession.commit

        async def _commit(session: AsyncSession) -> None:
            nonlocal commit_paused
            await original_commit(session)
            if not commit_paused:
                commit_paused = True
                first_commit_paused.set()
                await release_first_commit.wait()

        monkeypatch.setattr(AsyncSession, "commit", _commit)

        async def _poll() -> list[dict[str, Any]]:
            async with local_session() as db:
                executor = AsyncMock(spec=MacroAgentExecutor)
                executor.cancel.return_value = {}
                return await StuckExecutionPoller(
                    db, executor=executor, batch_size=5
                )._poll_pending_cancellations()

        first = asyncio.create_task(_poll())
        await asyncio.wait_for(first_commit_paused.wait(), timeout=5)
        second = asyncio.create_task(_poll())
        second_actions = await asyncio.wait_for(second, timeout=5)
        release_first_commit.set()
        first_actions = await asyncio.wait_for(first, timeout=5)

        completed = [
            action
            for action in first_actions + second_actions
            if action["action"] == "execution_cancel_completed"
        ]
        task_ids = [action["task_id"] for action in completed]
        assert len(completed) == 5
        assert len(task_ids) == len(set(task_ids))

    @pytest.mark.skipif(
        _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="SQLite cancellation concurrency test requires SQLite",
    )
    async def test_sqlite_pollers_claim_cancellation_once(
        self,
        isolated_db: tuple,
    ) -> None:
        """SQLite pollers must not call the external cancel operation twice."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            _task, _run_id = await _pending_cancel_task(seed)

        calls = 0
        first_call = asyncio.Event()
        second_call = asyncio.Event()
        release_call = asyncio.Event()

        class _CountingExecutor:
            async def cancel(self, _run_id: str) -> dict[str, Any]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_call.set()
                else:
                    second_call.set()
                await release_call.wait()
                return {}

        async def poll() -> list[dict[str, Any]]:
            async with local_session() as db:
                return await StuckExecutionPoller(
                    db,
                    executor=_CountingExecutor(),  # type: ignore[arg-type]
                )._poll_pending_cancellations()

        first = asyncio.create_task(poll())
        await asyncio.wait_for(first_call.wait(), timeout=5)
        second = asyncio.create_task(poll())
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second_call.wait(), timeout=0.25)
        finally:
            release_call.set()

        first_actions, second_actions = await asyncio.gather(first, second)

        assert calls == 1
        assert [
            action["action"]
            for action in first_actions + second_actions
        ] == ["execution_cancel_completed"]

    async def test_stale_cancellation_claim_is_reclaimed(
        self,
        isolated_db: tuple,
    ) -> None:
        """A claim older than its lease is eligible for recovery."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)
            execution = await seed.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert execution is not None
            execution.cancellation_claim_token = "stale-claim"
            execution.cancellation_claimed_at = (
                datetime.now(UTC) - CANCELLATION_CLAIM_LEASE - timedelta(seconds=1)
            )
            await seed.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.cancel.return_value = {}
        async with local_session() as db:
            actions = await StuckExecutionPoller(
                db, executor=executor
            )._poll_pending_cancellations()

        executor.cancel.assert_awaited_once_with(run_id)
        assert actions[0]["action"] == "execution_cancel_completed"
        async with local_session() as check:
            refreshed = await check.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert refreshed is not None
            assert refreshed.cancellation_pending is False
            assert refreshed.cancellation_claim_token is None
            assert refreshed.cancellation_claimed_at is None

    async def test_fresh_cancellation_claim_is_not_reclaimed(
        self,
        isolated_db: tuple,
    ) -> None:
        """A fresh claim stays owned by the worker doing external cleanup."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            task, _run_id = await _pending_cancel_task(seed)
            execution = await seed.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert execution is not None
            execution.cancellation_claim_token = "fresh-claim"
            execution.cancellation_claimed_at = datetime.now(UTC)
            await seed.commit()

        executor = AsyncMock(spec=MacroAgentExecutor)
        async with local_session() as db:
            actions = await StuckExecutionPoller(
                db, executor=executor
            )._poll_pending_cancellations()

        assert actions == []
        executor.cancel.assert_not_awaited()
        async with local_session() as check:
            refreshed = await check.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert refreshed is not None
            assert refreshed.cancellation_pending is True
            assert refreshed.cancellation_claim_token == "fresh-claim"

    async def test_cancelled_cancellation_claim_is_recoverable_after_lease(
        self,
        isolated_db: tuple,
    ) -> None:
        """A cancelled worker leaves a claim that a later poll can reclaim."""
        _engine, local_session = isolated_db

        async with local_session() as seed:
            task, run_id = await _pending_cancel_task(seed)

        cancelled_executor = AsyncMock(spec=MacroAgentExecutor)
        cancelled_executor.cancel.side_effect = asyncio.CancelledError()
        async with local_session() as db:
            with pytest.raises(asyncio.CancelledError):
                await StuckExecutionPoller(
                    db, executor=cancelled_executor
                )._poll_pending_cancellations()

        async with local_session() as check:
            claimed = await check.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert claimed is not None
            assert claimed.cancellation_pending is True
            assert claimed.cancellation_claim_token is not None
            assert claimed.cancellation_claimed_at is not None

        async with local_session() as advance:
            expired = await advance.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert expired is not None
            expired.cancellation_claimed_at = (
                datetime.now(UTC) - CANCELLATION_CLAIM_LEASE - timedelta(seconds=1)
            )
            await advance.commit()

        recovery_executor = AsyncMock(spec=MacroAgentExecutor)
        recovery_executor.cancel.return_value = {}
        async with local_session() as db:
            actions = await StuckExecutionPoller(
                db, executor=recovery_executor
            )._poll_pending_cancellations()

        recovery_executor.cancel.assert_awaited_once_with(run_id)
        assert actions[0]["action"] == "execution_cancel_completed"
        async with local_session() as check:
            recovered = await check.scalar(
                select(Execution).where(Execution.task_id == task.id)
            )
            assert recovered is not None
            assert recovered.cancellation_pending is False
            assert recovered.cancellation_claim_token is None
            completions = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task.id,
                        AuditLog.event_type == "execution_cancel_completed",
                    )
                )
            ).scalars().all()
            assert len(completions) == 1


class TestExecutionStartRecovery:
    async def test_retry_start_terminal_resolution_is_not_reopened(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#321: retry-start failure is terminal for the matching retry marker."""
        internal_id = str(uuid4())
        task_id = f"task-retry-terminal-{internal_id[:8]}"
        execution_id = f"execution-retry-terminal-{internal_id[:8]}"
        contract = TaskContract(
            task_id=task_id,
            project_id=f"proj-retry-terminal-{internal_id[:8]}",
            proposed_by="agent-1",
            objective="Keep a resolved retry terminal",
            acceptance=["the retry is not reopened"],
            execution={"timeout_minutes": 1, "max_retries": 1},
        )
        profile = ProjectProfile(
            project_id=contract.project_id,
            repository={"path": "/tmp/recovery-repo"},
            execution={"allowed_harnesses": ["opencode"]},
        )
        db_session.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                proposed_by=contract.proposed_by,
                state=TaskState.RUNNING,
                execution_attempts=1,
                latest_macro_agent_run_id=execution_id,
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        db_session.add(
            ProjectProfileModel(
                project_id=profile.project_id,
                profile_json=profile.model_dump(mode="json"),
            )
        )
        db_session.add(
            Execution(
                id=execution_id,
                task_id=task_id,
                state=TaskState.RUNNING,
                started_at=datetime.now(UTC) - timedelta(hours=2),
                macro_agent_run_id=None,
            )
        )
        db_session.add(
            AuditLog(
                event_id=f"evt-retry-terminal-{internal_id[:8]}",
                event_type="verification_retry_pending",
                task_id=task_id,
                actor="system",
                source="verification_service",
                timestamp=datetime.now(UTC) - timedelta(hours=2),
                payload={
                    "attempt": 1,
                    "max_retries": 1,
                    "verification_report": {"passed": False},
                },
            )
        )
        await db_session.commit()

        first_actions = await StuckExecutionPoller(db_session).poll()
        assert any(
            action["action"] == "failed_retry_start" for action in first_actions
        )

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "must-not-start"}
        second_actions = await StuckExecutionPoller(
            db_session, executor=executor
        ).poll()

        assert not any(
            action["action"] == "verification_retry_recovered"
            for action in second_actions
        )
        executor.start.assert_not_awaited()
        execution_count = await db_session.scalar(
            select(Execution).where(Execution.task_id == task_id).with_only_columns(
                Execution.id
            )
        )
        assert execution_count == execution_id
        failure = await db_session.scalar(
            select(AuditLog).where(
                AuditLog.task_id == task_id,
                AuditLog.event_type == "execution_start_failed",
            )
        )
        assert failure is not None
        assert failure.payload["attempt"] == 1

    async def test_missing_profile_is_retryable_and_backs_off(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#311: a missing profile is retryable; duplicate polls back off."""
        task, _contract = await _approved_task_with_pending_start(db_session)
        profile = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == task.project_id
            )
        )
        assert profile is not None
        await db_session.delete(profile)
        await db_session.commit()

        first_actions = await StuckExecutionPoller(db_session).poll()
        failed = [
            action
            for action in first_actions
            if action["action"] == "execution_start_recovery_failed"
        ]
        assert len(failed) == 1
        assert failed[0].get("retryable") is True

        # A second pass inside the backoff window must not produce another
        # recovery attempt.
        second_actions = await StuckExecutionPoller(db_session).poll()
        assert not any(
            action["action"] == "execution_start_recovery_failed"
            for action in second_actions
        )
        assert not any(
            action["action"] == "execution_start_recovered"
            for action in second_actions
        )

        # Restore the profile and bypass the backoff for the final pass.
        db_session.add(
            ProjectProfileModel(
                project_id=task.project_id,
                profile_json=ProjectProfile(
                    project_id=task.project_id,
                    repository={"path": "/tmp/recovery-repo"},
                    execution={"allowed_harnesses": ["opencode"]},
                ).model_dump(mode="json"),
            )
        )
        await db_session.commit()

        async def _no_backoff(*_args: object, **_kwargs: object) -> bool:
            return False

        monkeypatch.setattr(
            StuckExecutionPoller,
            "_execution_start_recovery_is_backing_off",
            _no_backoff,
            raising=False,
        )
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-after-backoff"}
        third_actions = await StuckExecutionPoller(
            db_session, executor=executor
        ).poll()
        assert any(
            action["action"] == "execution_start_recovered"
            for action in third_actions
        )
        executor.start.assert_awaited_once()

    async def test_malformed_contract_is_non_retryable_and_deduped(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#311: a malformed contract is terminal and never re-attempted."""
        internal_id = str(uuid4())
        task_id = f"task-malformed-start-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-malformed-start",
                proposed_by="agent-1",
                state=TaskState.EXEC_APPROVED,
                task_contract_json={"execution": "not-a-mapping"},
            )
        )
        db_session.add(
            AuditLog(
                event_id=f"evt-malformed-start-{internal_id[:8]}",
                event_type="execution_start_pending",
                task_id=task_id,
                actor="system",
                source="approval_service",
                timestamp=datetime.now(UTC) - timedelta(hours=2),
                payload={"operation": "execution_start"},
            )
        )
        await db_session.commit()

        first_actions = await StuckExecutionPoller(db_session).poll()
        failed = [
            action
            for action in first_actions
            if action["action"] == "execution_start_recovery_failed"
        ]
        assert len(failed) == 1
        assert failed[0].get("retryable") is False

        second_actions = await StuckExecutionPoller(db_session).poll()
        assert not any(
            action["action"] == "execution_start_recovery_failed"
            for action in second_actions
        )
        audits = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
        ).scalars().all()
        assert (
            len(
                [
                    audit
                    for audit in audits
                    if audit.event_type == "execution_start_recovery_failed"
                ]
            )
            == 1
        )

    async def test_macro_agent_flaky_failure_is_recorded_retryable(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#311: a macro-agent failure during recovery is logged retryable."""
        task, _contract = await _approved_task_with_pending_start(db_session)
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.side_effect = RuntimeError("macro-agent flaky")

        first_actions = await StuckExecutionPoller(
            db_session, executor=executor
        ).poll()
        failed = [
            action
            for action in first_actions
            if action["action"] == "execution_start_recovery_failed"
        ]
        assert len(failed) == 1
        assert failed[0].get("retryable") is True

        # The task leaves EXEC_APPROVED and is finalized as FAILED, so later
        # passes do not keep retrying the same marker.
        refreshed = await _fetch_task(db_session, task.id)
        assert refreshed.state == TaskState.FAILED

        second_actions = await StuckExecutionPoller(
            db_session, executor=executor
        ).poll()
        assert not any(
            action["action"] == "execution_start_recovery_failed"
            for action in second_actions
        )
        assert executor.start.await_count == 1


class TestPlaneProjectionSweeper:
    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="sweeper/producer lock-order test requires PostgreSQL",
    )
    async def test_sweeper_does_not_hold_audit_tip_while_waiting_for_next_event(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A batch sweep must not deadlock with the next marker's producer."""
        _engine, local_session = isolated_db
        task_ids = [
            "task-plane-lock-order-first",
            "task-plane-lock-order-second",
        ]
        pending_event_ids = [
            "pending-plane-lock-order-first",
            "pending-plane-lock-order-second",
        ]

        async with local_session() as seed:
            for task_id, pending_event_id in zip(
                task_ids, pending_event_ids, strict=True
            ):
                seed.add(
                    Task(
                        id=task_id,
                        project_id="proj-1",
                        proposed_by="agent-1",
                        state=TaskState.DONE,
                    )
                )
                seed.add(
                    AuditLog(
                        event_id=pending_event_id,
                        event_type="plane_projection_pending",
                        task_id=task_id,
                        actor="system",
                        source="reconciliation_service",
                        timestamp=datetime.now(UTC) - timedelta(hours=1),
                        payload={
                            "operation": "update_state",
                            "state": TaskState.RUNNING.value,
                        },
                    )
                )
            await seed.commit()

        second_sweeper_lock_requested = asyncio.Event()
        producer_holds_second_event = asyncio.Event()
        release_sweeper = asyncio.Event()

        from governance_controller.services import audit_service as audit_module
        from governance_controller.services import (
            stuck_execution_poller as poller_module,
        )

        original_audit_lock = audit_module.acquire_plane_projection_event_lock
        original_poller_lock = poller_module.acquire_plane_projection_event_lock

        async def observe_producer_lock(db: Any, pending_event_id: str) -> None:
            await original_audit_lock(db, pending_event_id)
            if pending_event_id == pending_event_ids[1]:
                producer_holds_second_event.set()

        async def pause_sweeper_lock(db: Any, pending_event_id: str) -> None:
            if pending_event_id == pending_event_ids[1]:
                second_sweeper_lock_requested.set()
                await release_sweeper.wait()
            await original_poller_lock(db, pending_event_id)

        monkeypatch.setattr(
            audit_module,
            "acquire_plane_projection_event_lock",
            observe_producer_lock,
        )
        monkeypatch.setattr(
            poller_module,
            "acquire_plane_projection_event_lock",
            pause_sweeper_lock,
        )

        async def produce_terminal() -> str:
            async with local_session() as producer:
                entry = await AuditService.log(
                    db=producer,
                    event_type="plane_projection_failed",
                    task_id=task_ids[1],
                    actor="system",
                    source="verification_service",
                    payload={"pending_event_id": pending_event_ids[1]},
                )
                await producer.commit()
                return entry.event_id

        sweeper_session = local_session()
        sweep_task = asyncio.create_task(
            StuckExecutionPoller(
                sweeper_session, batch_size=2
            )._poll_plane_projection_pending()
        )
        producer_task: asyncio.Task[str] | None = None
        try:
            await asyncio.wait_for(second_sweeper_lock_requested.wait(), timeout=5)
            producer_task = asyncio.create_task(produce_terminal())
            await asyncio.wait_for(producer_holds_second_event.wait(), timeout=5)
            release_sweeper.set()

            sweep_actions, producer_event_id = await asyncio.wait_for(
                asyncio.gather(sweep_task, producer_task), timeout=2
            )
        except TimeoutError as exc:
            raise AssertionError(
                "sweeper and producer deadlocked on audit tip/event locks"
            ) from exc
        finally:
            release_sweeper.set()
            if not sweep_task.done():
                sweep_task.cancel()
            if producer_task is not None and not producer_task.done():
                producer_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)
            if producer_task is not None:
                await asyncio.gather(producer_task, return_exceptions=True)
            await sweeper_session.close()

        assert len(
            [
                action
                for action in sweep_actions
                if action["action"] == "plane_projection_resolved"
            ]
        ) == 1
        async with local_session() as check:
            rows = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_ids[1],
                        AuditLog.payload["pending_event_id"].as_string()
                        == pending_event_ids[1],
                    )
                )
            ).scalars().all()
            assert {row.event_id for row in rows} == {producer_event_id}

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="sweeper/producer interleaving requires PostgreSQL",
    )
    async def test_producer_completion_wins_after_sweeper_claims_marker(
        self,
        isolated_db: tuple,
    ) -> None:
        """A producer completing after claim must prevent sweeper insertion."""
        _engine, local_session = isolated_db
        task_id = "task-plane-sweep-producer-race"
        pending_event_id = "pending-plane-sweep-producer-race"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            seed.add(
                AuditLog(
                    event_id=pending_event_id,
                    event_type="plane_projection_pending",
                    task_id=task_id,
                    actor="system",
                    source="reconciliation_service",
                    timestamp=datetime.now(UTC) - timedelta(hours=1),
                    payload={
                        "operation": "update_state",
                        "state": TaskState.EXEC_APPROVED.value,
                    },
                )
            )
            await seed.commit()

        marker_claimed = asyncio.Event()
        release_sweeper = asyncio.Event()
        sweeper_session = local_session()
        original_execute = sweeper_session.execute

        async def execute_and_pause(
            statement: Any, *args: Any, **kwargs: Any
        ) -> Any:
            result = await original_execute(statement, *args, **kwargs)
            if getattr(statement, "_for_update_arg", None) is not None:
                marker_claimed.set()
                await release_sweeper.wait()
            return result

        sweeper_session.execute = execute_and_pause  # type: ignore[method-assign]
        sweep_task = asyncio.create_task(
            StuckExecutionPoller(
                sweeper_session, batch_size=1
            )._poll_plane_projection_pending()
        )
        producer_task: asyncio.Task[str] | None = None
        try:
            await asyncio.wait_for(marker_claimed.wait(), timeout=5)

            async def produce_terminal() -> str:
                async with local_session() as producer:
                    entry = await AuditService.log(
                        db=producer,
                        event_type="plane_projection_completed",
                        task_id=task_id,
                        actor="system",
                        source="verification_service",
                        payload={"pending_event_id": pending_event_id},
                    )
                    await producer.commit()
                    return entry.event_id

            producer_task = asyncio.create_task(produce_terminal())
            await asyncio.wait_for(producer_task, timeout=5)
            release_sweeper.set()
            sweep_actions = await asyncio.wait_for(sweep_task, timeout=5)
        finally:
            release_sweeper.set()
            if producer_task is not None and not producer_task.done():
                producer_task.cancel()
            if not sweep_task.done():
                sweep_task.cancel()
            await asyncio.gather(producer_task, sweep_task, return_exceptions=True)
            await sweeper_session.close()

        assert sweep_actions == []
        async with local_session() as check:
            rows = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_id,
                        AuditLog.event_type == "plane_projection_completed",
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].payload["pending_event_id"] == pending_event_id

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="concurrent sweeper locking requires PostgreSQL",
    )
    async def test_concurrent_sweepers_complete_pending_marker_once(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#330: one pending marker produces one completion audit."""
        engine, local_session = isolated_db
        internal_id = str(uuid4())
        task_id = f"task-plane-sweep-race-{internal_id[:8]}"
        pending_event_id = f"evt-plane-sweep-race-{internal_id[:8]}"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            seed.add(
                AuditLog(
                    event_id=pending_event_id,
                    event_type="plane_projection_pending",
                    task_id=task_id,
                    actor="system",
                    source="reconciliation_service",
                    timestamp=datetime.now(UTC) - timedelta(hours=1),
                    payload={
                        "operation": "update_state",
                        "state": TaskState.EXEC_APPROVED.value,
                    },
                )
            )
            await seed.commit()

        first_completion_entered = asyncio.Event()
        second_poller_finished = asyncio.Event()
        completion_calls = 0
        original_log = AuditService.log

        async def _gated_log(*args: Any, **kwargs: Any) -> AuditLog:
            nonlocal completion_calls
            if kwargs.get("event_type") == "plane_projection_completed":
                completion_calls += 1
                if completion_calls == 1:
                    first_completion_entered.set()
                    await second_poller_finished.wait()
            return await original_log(*args, **kwargs)

        monkeypatch.setattr(AuditService, "log", staticmethod(_gated_log))

        async def _poll() -> list[dict[str, Any]]:
            async with local_session() as db:
                actions = await StuckExecutionPoller(
                    db,
                    batch_size=1,
                )._poll_plane_projection_pending()
            second_poller_finished.set()
            return actions

        first = asyncio.create_task(_poll())
        second: asyncio.Task[list[dict[str, Any]]] | None = None
        try:
            await asyncio.wait_for(first_completion_entered.wait(), timeout=5)
            second = asyncio.create_task(_poll())
            await asyncio.wait_for(second_poller_finished.wait(), timeout=5)
            first_actions = await asyncio.wait_for(first, timeout=5)
            assert second is not None
            second_actions = await asyncio.wait_for(second, timeout=5)
        finally:
            second_poller_finished.set()
            pending_tasks = [first]
            if second is not None:
                pending_tasks.append(second)
            for poller_task in pending_tasks:
                if not poller_task.done():
                    poller_task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        actions = first_actions + second_actions
        assert [action["action"] for action in actions] == [
            "plane_projection_resolved"
        ]
        assert len(
            [
                action
                for action in actions
                if action["action"] == "plane_projection_resolved"
            ]
        ) == 1
        assert completion_calls == 1

        async with local_session() as check:
            completed = (
                await check.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_id,
                        AuditLog.event_type == "plane_projection_completed",
                    )
                )
            ).scalars().all()
            assert len(completed) == 1
            assert completed[0].payload["pending_event_id"] == pending_event_id

    async def test_terminal_failure_alert_is_not_resolved_by_terminal_state(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#322: terminal state does not prove a human alert was delivered."""
        internal_id = str(uuid4())
        task_id = f"task-terminal-alert-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.FAILED,
            )
        )
        pending = AuditLog(
            event_id=f"evt-terminal-alert-{internal_id[:8]}",
            event_type="plane_projection_pending",
            task_id=task_id,
            actor="system",
            source="verification_service",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
            payload={
                "operation": "terminal_failure_alert",
                "reason": "max_retries_exhausted",
            },
        )
        db_session.add(pending)
        await db_session.commit()

        actions = await StuckExecutionPoller(db_session).poll()

        assert not any(
            action.get("action") == "plane_projection_resolved"
            for action in actions
        )
        audits = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
        ).scalars().all()
        assert not any(
            audit.event_type == "plane_projection_completed"
            and audit.payload.get("pending_event_id") == pending.event_id
            for audit in audits
        )

    async def test_reconciliation_state_fix_marker_is_swept_when_state_moves_on(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#325: reconciliation markers use the same stale-state rule."""
        internal_id = str(uuid4())
        task_id = f"task-reconciliation-sweep-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.DONE,
            )
        )
        pending = AuditLog(
            event_id=f"evt-reconciliation-sweep-{internal_id[:8]}",
            event_type="plane_projection_pending",
            task_id=task_id,
            actor="system",
            source="reconciliation_service",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
            payload={
                "operation": "reconciliation_state_fix",
                "state": TaskState.EXEC_APPROVED.value,
            },
        )
        db_session.add(pending)
        await db_session.commit()

        actions = await StuckExecutionPoller(db_session).poll()

        assert any(
            action.get("action") == "plane_projection_resolved"
            and action.get("pending_event_id") == pending.event_id
            for action in actions
        )
    async def test_sweeper_resolves_orphaned_plane_projection_pending(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#314: orphaned plane_projection_pending markers are resolved."""
        internal_id = str(uuid4())
        task_id = f"task-plane-sweep-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.RUNNING,
            )
        )
        await db_session.flush()
        pending = AuditLog(
            event_id=f"evt-plane-pending-{internal_id[:8]}",
            event_type="plane_projection_pending",
            task_id=task_id,
            actor="system",
            source="reconciliation_service",
            timestamp=datetime.now(UTC) - timedelta(hours=1),
            payload={
                "operation": "update_state",
                "state": TaskState.EXEC_APPROVED.value,
            },
        )
        db_session.add(pending)
        await db_session.commit()

        dry_actions = await StuckExecutionPoller(
            db_session, dry_run=True
        ).poll()
        dry_sweep = [
            action
            for action in dry_actions
            if action.get("action") == "would_resolve_plane_projection_pending"
        ]
        assert len(dry_sweep) == 1
        assert dry_sweep[0]["pending_event_id"] == pending.event_id

        actions = await StuckExecutionPoller(db_session).poll()
        sweep = [
            action
            for action in actions
            if action.get("action") == "plane_projection_resolved"
        ]
        assert len(sweep) == 1
        assert sweep[0]["task_id"] == task_id

        audits = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
        ).scalars().all()
        completed = [
            audit
            for audit in audits
            if audit.event_type == "plane_projection_completed"
        ]
        assert len(completed) == 1
        assert completed[0].payload["reason"] == "resolved_independently"
        assert completed[0].payload["pending_event_id"] == pending.event_id

    async def test_synthetic_completion_does_not_hide_real_failure(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A sweeper marker cannot suppress a later authoritative failure."""
        internal_id = str(uuid4())
        task_id = f"task-plane-real-failure-{internal_id[:8]}"
        pending_event_id = f"evt-plane-real-failure-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.DONE,
            )
        )
        db_session.add(
            AuditLog(
                event_id=pending_event_id,
                event_type="plane_projection_pending",
                task_id=task_id,
                actor="system",
                source="reconciliation_service",
                timestamp=datetime.now(UTC) - timedelta(hours=1),
                payload={
                    "operation": "update_state",
                    "state": TaskState.RUNNING.value,
                },
            )
        )
        await db_session.commit()

        actions = await StuckExecutionPoller(db_session).poll()
        assert any(
            action["action"] == "plane_projection_resolved" for action in actions
        )

        failed = await AuditService.log(
            db=db_session,
            event_type="plane_projection_failed",
            task_id=task_id,
            actor="system",
            source="verification_service",
            payload={
                "pending_event_id": pending_event_id,
                "error": "Plane unavailable",
            },
        )
        await db_session.commit()

        assert failed.event_type == "plane_projection_failed"
        audits = (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.task_id == task_id)
                .order_by(AuditLog.id)
            )
        ).scalars().all()
        assert [
            audit.event_type
            for audit in audits
            if audit.event_type
            in {
                "plane_projection_completed",
                "plane_projection_failed",
            }
            if audit.payload.get("pending_event_id") == pending_event_id
        ] == ["plane_projection_completed", "plane_projection_failed"]

    async def test_sweeper_ignores_recent_plane_projection_pending(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#314: in-flight/recent markers are not swept."""
        internal_id = str(uuid4())
        task_id = f"task-plane-recent-{internal_id[:8]}"
        db_session.add(
            Task(
                id=task_id,
                project_id="proj-1",
                proposed_by="agent-1",
                state=TaskState.RUNNING,
            )
        )
        await db_session.flush()
        db_session.add(
            AuditLog(
                event_id=f"evt-plane-recent-{internal_id[:8]}",
                event_type="plane_projection_pending",
                task_id=task_id,
                actor="system",
                source="reconciliation_service",
                timestamp=datetime.now(UTC) - timedelta(seconds=10),
                payload={
                    "operation": "update_state",
                    "state": TaskState.EXEC_APPROVED.value,
                },
            )
        )
        await db_session.commit()

        actions = await StuckExecutionPoller(db_session).poll()
        assert not any(
            action.get("action") == "plane_projection_resolved"
            for action in actions
        )


class _FailingMacroAgentClient:
    async def status(self, run_id: str) -> dict[str, Any]:
        raise RuntimeError("macro-agent unreachable")
