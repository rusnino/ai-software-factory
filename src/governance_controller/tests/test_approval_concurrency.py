"""Concurrency regression tests for approval paths.

The tests use the ``isolated_db`` fixture so they run identically against the
configured test database (SQLite by default, PostgreSQL via
``GC_TEST_DATABASE_URL``). The deterministic CAS-loss scenarios exercise the
production guard without requiring true interleaved concurrency.
"""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.models.approval import Approval
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.state_machine import StateMachine


def _make_contract(task_id: str = "task-concurrent") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        project_id="proj-1",
        proposed_by="agent-1",
        objective="Implement feature X",
        acceptance=["feature X passes tests"],
        execution=ExecutionConfig(harness="opencode"),
        forbidden_paths=[],
    )


def _make_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": ["opencode"]},
        security={"forbidden_paths": []},
    )


async def _seed_task(db: AsyncSession, task_id: str) -> None:
    task = Task(
        id=task_id,
        project_id="proj-1",
        state=TaskState.PLAN_APPROVED,
        proposed_by="agent-1",
    )
    db.add(task)
    await db.commit()


class TestApprovalConcurrency:
    async def test_cancelled_execution_handoff_leaves_recoverable_marker(
        self,
        isolated_db: tuple,
        patched_db,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cancellation after approval must leave a durable start intent."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        _engine, local_session = isolated_db
        task_id = "task-cancelled-execution-start"

        async with local_session() as seed:
            await _seed_task(seed, task_id)

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        service = ApprovalService(db=None, executor=fake_executor)  # type: ignore[arg-type]

        async def _cancel_before_start(*_args: object, **_kwargs: object) -> Task:
            raise asyncio.CancelledError

        service._trigger_execution = _cancel_before_start  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            async with asynccontextmanager(get_db)() as db:
                service.db = db
                task = await db.scalar(select(Task).where(Task.id == task_id))
                assert task is not None
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-cancelled-execution-start",
                )

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.EXEC_APPROVED
            approval = await check.scalar(
                select(Approval).where(Approval.task_id == task_id)
            )
            assert approval is not None
            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "execution_start_pending" for row in audits
            )

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_slow_older_plane_projection_cannot_overwrite_newer_state(
        self,
        isolated_db: tuple,
    ) -> None:
        """#283: a later state projection must win over an older slow request."""

        class _OrderedProjection:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.running_started = asyncio.Event()
                self.states: list[TaskState] = []

            async def update_state(self, **kwargs: object) -> dict[str, object]:
                state = kwargs["state"]
                assert isinstance(state, TaskState)
                if state == TaskState.PLAN_APPROVED:
                    self.started.set()
                    await self.release.wait()
                elif state == TaskState.RUNNING:
                    self.running_started.set()
                self.states.append(state)
                return {}

        _engine, local_session = isolated_db
        projection = _OrderedProjection()
        contract = _make_contract("task-plane-order-283")
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-plane-order-283"}

        async with local_session() as seed:
            seed.add(
                Task(
                    id=contract.task_id,
                    project_id=contract.project_id,
                    state=TaskState.PROPOSED,
                    proposed_by=contract.proposed_by,
                )
            )
            await seed.commit()

        session_a = local_session()
        plan_task = None
        try:
            task_a = await session_a.scalar(
                select(Task).where(Task.id == contract.task_id)
            )
            assert task_a is not None
            plan_task = asyncio.create_task(
                ApprovalService(
                    db=session_a,
                    plane_projection=projection,  # type: ignore[arg-type]
                ).approve(
                    task=task_a,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.PLAN,
                    source="test",
                    actor="admin",
                    idempotency_key="key-plane-plan-283",
                )
            )
            await projection.started.wait()

            async def _approve_execution() -> Task:
                async with local_session() as session_b:
                    task_b = await session_b.scalar(
                        select(Task).where(Task.id == contract.task_id)
                    )
                    assert task_b is not None
                    result = await ApprovalService(
                        db=session_b,
                        executor=fake_executor,
                        plane_projection=projection,  # type: ignore[arg-type]
                    ).approve(
                        task=task_b,
                        contract=contract,
                        profile=profile,
                        approval_type=ApprovalType.EXECUTION,
                        source="test",
                        actor="admin",
                        idempotency_key="key-plane-execution-283",
                    )
                    await session_b.commit()
                    return result

            execution_task = asyncio.create_task(_approve_execution())
            for _ in range(100):
                async with local_session() as observer:
                    current = await observer.scalar(
                        select(Task).where(Task.id == contract.task_id)
                    )
                if current is not None and current.state in {
                    TaskState.EXEC_APPROVED,
                    TaskState.RUNNING,
                }:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("execution approval never committed EXEC_APPROVED")

            # Without a per-task projection lock, the newer RUNNING projection
            # completes before the older PLAN projection is released. A correct
            # lock implementation keeps it waiting here until PLAN completes.
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(projection.running_started.wait(), 1.0)
            projection.release.set()
            await plan_task
            result = await execution_task
            assert result.state == TaskState.RUNNING
            assert projection.states[-1] == TaskState.RUNNING
        finally:
            projection.release.set()
            if plan_task is not None and not plan_task.done():
                await plan_task
            if "execution_task" in locals() and not execution_task.done():
                await execution_task
            await session_a.close()

    async def test_second_approval_after_commit_fails_with_concurrent_modification(
        self,
        isolated_db: tuple,
    ) -> None:
        """A stale read followed by a committed advance must be rejected.

        This is the deterministic equivalent of the race: session A reads the
        task, session B commits an approval that advances the task, and then
        session A's approval sees rowcount == 0 and raises.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-stale")

        contract = _make_contract("task-stale")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-first"}

        # Session A reads the task but does not commit yet.
        session_a = local_session()
        task_a = await session_a.scalar(select(Task).where(Task.id == "task-stale"))
        assert task_a is not None
        assert task_a.state == TaskState.PLAN_APPROVED
        assert task_a.version == 0

        # Session B approves and commits first.
        async with local_session() as session_b:
            service_b = ApprovalService(db=session_b, executor=fake_executor)
            task_b = await session_b.scalar(select(Task).where(Task.id == "task-stale"))
            assert task_b is not None
            result = await service_b.approve(
                task=task_b,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-first",
            )
            assert result.state == TaskState.RUNNING
            await session_b.commit()

        # Session A now tries to approve its stale copy.
        service_a = ApprovalService(db=session_a, executor=fake_executor)
        with pytest.raises(ValueError, match="Concurrent modification detected"):
            await service_a.approve(
                task=task_a,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-second",
            )
        await session_a.close()

        # Only one execution and one approval were recorded.
        async with local_session() as check:
            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-stale")
            )
            approvals = await check.execute(
                select(Approval).where(Approval.task_id == "task-stale")
            )
            assert len(executions.scalars().all()) == 1
            assert len(approvals.scalars().all()) == 1

            task = await check.scalar(select(Task).where(Task.id == "task-stale"))
            assert task is not None
            assert task.state == TaskState.RUNNING
            # Successful EXECUTION approval performs 3 CAS increments:
            # PLAN_APPROVED -> EXEC_APPROVED, EXEC_APPROVED -> READY,
            # READY -> RUNNING.
            assert task.version == 3

    async def test_duplicate_delivery_returns_true_current_state_not_stale(
        self,
        isolated_db: tuple,
    ) -> None:
        """#278: a duplicate-delivery re-fetch must bypass the identity map.

        Session A makes a real PLAN approval and stays open (identity-mapping
        the task at PLAN_APPROVED). A genuinely separate session B then
        drives the task all the way to RUNNING via a real EXECUTION approval
        and commits. Session A then redelivers its ORIGINAL PLAN approval
        request (same idempotency_key/approval_type/actor) — this must hit
        the idempotent-duplicate branch and return the TRUE current state
        (RUNNING), not session A's stale in-memory PLAN_APPROVED copy. This
        re-fetch was added specifically to fix #242 in an earlier round; using
        ``db.get()`` meant it silently never worked, since the task was
        already identity-mapped in the same session from the first call.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-duplicate-278",
                project_id="proj-1",
                state=TaskState.PROPOSED,
                proposed_by="agent-1",
            )
            seed.add(task)
            await seed.commit()

        contract = _make_contract("task-duplicate-278")
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-duplicate-278"}

        # Session A makes the first, real PLAN approval and stays open.
        session_a = local_session()
        task_a = await session_a.scalar(
            select(Task).where(Task.id == "task-duplicate-278")
        )
        assert task_a is not None
        service_a = ApprovalService(db=session_a, executor=fake_executor)
        first_result = await service_a.approve(
            task=task_a,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="test",
            actor="admin",
            idempotency_key="key-plan-278",
        )
        assert first_result.state == TaskState.PLAN_APPROVED
        await session_a.commit()

        # A genuinely separate session drives the task all the way to
        # RUNNING via a real EXECUTION approval and commits.
        async with local_session() as session_b:
            task_b = await session_b.scalar(
                select(Task).where(Task.id == "task-duplicate-278")
            )
            assert task_b is not None
            service_b = ApprovalService(db=session_b, executor=fake_executor)
            exec_result = await service_b.approve(
                task=task_b,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-exec-278",
            )
            assert exec_result.state == TaskState.RUNNING
            await session_b.commit()

        # Session A redelivers its ORIGINAL PLAN approval request.
        duplicate_result = await service_a.approve(
            task=task_a,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="test",
            actor="admin",
            idempotency_key="key-plan-278",
        )

        assert duplicate_result.state == TaskState.RUNNING, (
            "duplicate delivery must return the TRUE current state, not "
            "session A's stale in-memory PLAN_APPROVED copy"
        )

        audits = await session_a.execute(
            select(AuditLog).where(
                AuditLog.task_id == "task-duplicate-278",
                AuditLog.event_type == "approval_idempotent",
            )
        )
        idempotent_entries = audits.scalars().all()
        assert len(idempotent_entries) == 1
        payload = idempotent_entries[0].payload
        assert payload["previous_state"] == TaskState.RUNNING.value
        assert payload["new_state"] == TaskState.RUNNING.value

        await session_a.close()

    async def test_executor_failure_advances_to_failed_with_version_3(
        self,
        isolated_db: tuple,
    ) -> None:
        """If executor.start raises, the task ends at FAILED with version 3.

        The CAS increments are PLAN_APPROVED -> EXEC_APPROVED,
        EXEC_APPROVED -> READY, READY -> FAILED, so the final version is 3.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-exec-fail")

        contract = _make_contract("task-exec-fail")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = RuntimeError("boom")

        async with local_session() as db:
            service = ApprovalService(db=db, executor=fake_executor)
            task = await db.scalar(select(Task).where(Task.id == "task-exec-fail"))
            assert task is not None
            # Let the service raise and the session rollback naturally; do not
            # manually commit -- that is production code's responsibility.
            with pytest.raises(RuntimeError, match="macro-agent start failed"):
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-exec-fail",
                )

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == "task-exec-fail"))
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.version == 3

            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-exec-fail")
            )
            assert len(executions.scalars().all()) == 1

    async def test_malformed_start_response_is_audited_and_fails_task(
        self,
        isolated_db: tuple,
    ) -> None:
        """#285: an invalid macro-agent response must use the failure path."""
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-malformed-start")

        contract = _make_contract("task-malformed-start")
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"status": "queued"}

        async with local_session() as db:
            task = await db.scalar(
                select(Task).where(Task.id == "task-malformed-start")
            )
            assert task is not None
            service = ApprovalService(db=db, executor=fake_executor)
            with pytest.raises(RuntimeError, match="macro-agent start failed"):
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-malformed-start",
                )

        async with local_session() as check:
            task = await check.scalar(
                select(Task).where(Task.id == "task-malformed-start")
            )
            assert task is not None
            assert task.state == TaskState.FAILED

            executions = await check.execute(
                select(Execution).where(Execution.task_id == task.id)
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == task.id)
            )
            assert any(
                row.event_type == "execution_start_failed"
                for row in audits.scalars().all()
            )

    async def test_start_failure_after_terminal_task_race_finalizes_execution(
        self,
        isolated_db: tuple,
    ) -> None:
        """A failed start must not leave a terminal task's execution active."""
        _engine, local_session = isolated_db
        task_id = "task-start-failure-terminal-race"

        async with local_session() as seed:
            await _seed_task(seed, task_id)

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)

        async def _race_to_terminal_then_fail(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.FAILED
                )
                assert won is True
                await racer.commit()
            raise RuntimeError("macro-agent unavailable")

        fake_executor.start.side_effect = _race_to_terminal_then_fail

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(RuntimeError, match="macro-agent start failed"):
                await ApprovalService(
                    db=db,
                    executor=fake_executor,
                ).approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-start-failure-terminal-race",
                )

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.FAILED
            assert execution.ended_at is not None

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "execution_start_failed" for row in audits
            )

    async def test_concurrent_execution_approvals_do_not_double_trigger(
        self,
        isolated_db: tuple,
    ) -> None:
        """Two approvals racing for the same task must produce exactly one run.

        Because SQLite's async driver does not serialize the interleaved read-
        update-read-update pattern deterministically, this test uses an
        ``asyncio.Lock`` only to order the two approvals: the first caller
        reads, approves, and commits; the second caller reads after the lock is
        released and therefore observes the updated state, failing before the
        executor can be started a second time. The lock is a test-only
        sequencing device; the production guard is the atomic UPDATE above.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-concurrent")

        contract = _make_contract()
        profile = _make_profile()
        start_counts: list[int] = []
        errors: list[Exception] = []
        order_lock = asyncio.Lock()

        async def _approve(idempotency_key: str) -> Task | Exception:
            fake_executor = AsyncMock(spec=MacroAgentExecutor)
            fake_executor.start.return_value = {"run_id": f"run-{idempotency_key}"}

            async with local_session() as db:
                service = ApprovalService(db=db, executor=fake_executor)
                async with order_lock:
                    task = await db.scalar(
                        select(Task).where(Task.id == "task-concurrent")
                    )
                assert task is not None
                try:
                    result = await service.approve(
                        task=task,
                        contract=contract,
                        profile=profile,
                        approval_type=ApprovalType.EXECUTION,
                        source="test",
                        actor="admin",
                        idempotency_key=idempotency_key,
                    )
                    await db.commit()
                    start_counts.append(fake_executor.start.await_count)
                    return result
                except Exception as exc:  # noqa: BLE001
                    start_counts.append(fake_executor.start.await_count)
                    return exc

        results = await asyncio.gather(
            _approve("key-concurrent-1"),
            _approve("key-concurrent-2"),
        )

        successes = [r for r in results if isinstance(r, Task)]
        failures = [r for r in results if isinstance(r, Exception)]
        errors.extend(failures)

        assert len(successes) == 1
        assert len(errors) == 1
        # The loser may fail either at the in-memory state-machine check
        # (now seeing READY) or at the atomic UPDATE CAS; both prove the
        # production guard prevented a second execution start.
        assert sum(start_counts) == 1

        async with local_session() as check:
            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-concurrent")
            )
            approvals = await check.execute(
                select(Approval).where(Approval.task_id == "task-concurrent")
            )
            assert len(executions.scalars().all()) == 1
            assert len(approvals.scalars().all()) == 1

            task = await check.scalar(select(Task).where(Task.id == "task-concurrent"))
            assert task is not None
            assert task.state == TaskState.RUNNING

    async def test_executor_failure_path_survives_get_db_rollback(
        self,
        isolated_db: tuple,
        patched_db,
    ) -> None:
        """Executor crash persists FAILED state + audit via real get_db() semantics.

        ``get_db()`` rolls back on any exception, so if the service raises
        without committing, the EXEC_APPROVED transition, Execution row, and
        FAILED transition all vanish. This test drives the actual dependency
        generator to prove the production path now leaves a durable FAILED
        state and audit trail.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-get-db-fail")

        contract = _make_contract("task-get-db-fail")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = RuntimeError("boom")

        service = ApprovalService(db=None, executor=fake_executor)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="macro-agent start failed"):
            async with asynccontextmanager(get_db)() as db:
                service.db = db
                task = await db.scalar(
                    select(Task).where(Task.id == "task-get-db-fail")
                )
                assert task is not None
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-get-db-fail",
                )

        # Query from a fresh session to ensure state is truly persisted.
        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == "task-get-db-fail"))
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.version == 3

            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-get-db-fail")
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-get-db-fail")
            )
            events = [a.event_type for a in audits.scalars().all()]
            assert "approval" in events
            assert "state_change" in events
            assert "execution_start_failed" in events

    async def test_outer_cas_loss_survives_get_db_rollback(
        self,
        isolated_db: tuple,
        patched_db,
    ) -> None:
        """Losing the outer ``approve()`` CAS persists its audit and winner state.

        Session A reads PLAN_APPROVED. Session B wins the EXECUTION approval
        through READY and RUNNING. Session A then drives the real ``get_db()``
        generator and loses the ``PLAN_APPROVED -> EXEC_APPROVED`` CAS inside
        ``approve()`` (before ``_trigger_execution`` runs); its audit row must
        survive the rollback and the database must reflect session B's RUNNING
        state.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-ready-cas")

        contract = _make_contract("task-ready-cas")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-winner"}

        # Loser: start a session and read the task BEFORE the winner commits,
        # so session_a's identity map still holds PLAN_APPROVED.
        session_a = local_session()
        task_a = await session_a.scalar(select(Task).where(Task.id == "task-ready-cas"))
        assert task_a is not None
        assert task_a.state == TaskState.PLAN_APPROVED
        assert task_a.version == 0

        # Winner: session B completes the full EXECUTION approval first.
        async with local_session() as session_b:
            service_b = ApprovalService(db=session_b, executor=fake_executor)
            task_b = await session_b.scalar(
                select(Task).where(Task.id == "task-ready-cas")
            )
            assert task_b is not None
            result = await service_b.approve(
                task=task_b,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-winner",
            )
            assert result.state == TaskState.RUNNING
            await session_b.commit()

        service_a = ApprovalService(db=None, executor=fake_executor)  # type: ignore[arg-type]

        try:
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                async with asynccontextmanager(get_db)() as db:
                    service_a.db = db
                    await service_a.approve(
                        task=task_a,
                        contract=contract,
                        profile=profile,
                        approval_type=ApprovalType.EXECUTION,
                        source="test",
                        actor="admin",
                        idempotency_key="key-loser",
                    )
        finally:
            await session_a.close()

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == "task-ready-cas"))
            assert task is not None
            assert task.state == TaskState.RUNNING
            assert task.version == 3

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-ready-cas")
            )
            events = {a.event_type for a in audits.scalars().all()}
            # The loser fails at the PLAN_APPROVED -> EXEC_APPROVED CAS in
            # approve() before _trigger_execution runs, so it records an
            # approval_rejected event. The winner records the normal flow.
            assert "approval_rejected" in events
            assert "approval" in events

            approvals = await check.execute(
                select(Approval).where(Approval.task_id == "task-ready-cas")
            )
            assert len(approvals.scalars().all()) == 1

            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-ready-cas")
            )
            assert len(executions.scalars().all()) == 1

    async def test_trigger_execution_ready_cas_loss_commits_before_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
        isolated_db: tuple,
        patched_db,
    ) -> None:
        """Losing the READY CAS inside ``_trigger_execution`` persists audit.

        The task is seeded at ``PLAN_APPROVED``. ``approve()`` succeeds through
        ``EXEC_APPROVED``, but the ``EXEC_APPROVED -> READY`` CAS inside
        ``_trigger_execution`` is forced to lose. The commit-before-raise must
        leave the task at ``EXEC_APPROVED`` with a durable
        ``concurrent_modification`` audit row.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-ready-cas-internal")

        contract = _make_contract("task-ready-cas-internal")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-ready-cas"}

        # Force only the READY-CAS inside _trigger_execution to lose.
        original = StateMachine.atomic_transition

        async def _patched(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.READY:
                return False
            return await original(db, task, target_state)

        monkeypatch.setattr(StateMachine, "atomic_transition", staticmethod(_patched))

        service = ApprovalService(db=None, executor=fake_executor)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Concurrent modification detected"):
            async with asynccontextmanager(get_db)() as db:
                service.db = db
                task = await db.scalar(
                    select(Task).where(Task.id == "task-ready-cas-internal")
                )
                assert task is not None
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-ready-cas-internal",
                )

        async with local_session() as check:
            task = await check.scalar(
                select(Task).where(Task.id == "task-ready-cas-internal")
            )
            assert task is not None
            assert task.state == TaskState.EXEC_APPROVED

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-ready-cas-internal")
            )
            events = [a.event_type for a in audits.scalars().all()]
            assert "concurrent_modification" in events

            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-ready-cas-internal")
            )
            assert len(executions.scalars().all()) == 0

    async def test_trigger_execution_running_cas_loss_commits_before_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
        isolated_db: tuple,
        patched_db,
    ) -> None:
        """Losing the RUNNING CAS inside ``_trigger_execution`` persists audit.

        ``approve()`` reaches ``_trigger_execution``, the executor starts, but
        the ``READY -> RUNNING`` CAS loses. The commit-before-raise must leave
        the task at ``READY`` while terminalizing the losing Execution row (so
        a real macro-agent run is not orphaned as RUNNING for a FAILED/READY
        task) and preserving the ``concurrent_modification`` audit row (#262).
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            await _seed_task(seed, "task-running-cas-internal")

        contract = _make_contract("task-running-cas-internal")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-running-cas"}

        original = StateMachine.atomic_transition

        async def _patched(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original(db, task, target_state)

        monkeypatch.setattr(StateMachine, "atomic_transition", staticmethod(_patched))

        service = ApprovalService(db=None, executor=fake_executor)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Concurrent modification detected"):
            async with asynccontextmanager(get_db)() as db:
                service.db = db
                task = await db.scalar(
                    select(Task).where(Task.id == "task-running-cas-internal")
                )
                assert task is not None
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-running-cas-internal",
                )

        async with local_session() as check:
            task = await check.scalar(
                select(Task).where(Task.id == "task-running-cas-internal")
            )
            assert task is not None
            assert task.state == TaskState.READY

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-running-cas-internal")
            )
            events = [a.event_type for a in audits.scalars().all()]
            assert "concurrent_modification" in events

            executions = await check.execute(
                select(Execution).where(
                    Execution.task_id == "task-running-cas-internal"
                )
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None
            assert rows[0].macro_agent_run_id == "run-running-cas"
            assert rows[0].cancellation_pending is False
            fake_executor.cancel.assert_awaited_once_with("run-running-cas")

    async def test_running_cas_loss_cancels_run_when_terminal_task_keeps_pointer(
        self,
        isolated_db: tuple,
    ) -> None:
        """A terminal task must not make a matching external run look owned."""
        _engine, local_session = isolated_db
        task_id = "task-terminal-pointer-cancel"
        run_id = "run-terminal-pointer-cancel"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    state=TaskState.EXEC_APPROVED,
                    proposed_by="agent-1",
                )
            )
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)

        async def _start_then_terminalize(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.state == TaskState.READY.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        fake_executor.start.side_effect = _start_then_terminalize
        fake_executor.cancel.return_value = {}

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await ApprovalService(db=db, executor=fake_executor)._trigger_execution(
                    task=task,
                    contract=contract,
                    profile=profile,
                    actor="admin",
                    source="test",
                    previous_state=TaskState.EXEC_APPROVED,
                )

        fake_executor.cancel.assert_awaited_once_with(run_id)

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="poller-first interleave requires PostgreSQL row locks",
    )
    async def test_running_cas_cleanup_rechecks_pending_claim_after_poller_cleanup(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Approval must not duplicate a poller's durable cancellation outcome."""
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        _engine, local_session = isolated_db
        task_id = "task-approval-cleanup-poller-race"
        run_id = "run-approval-cleanup-poller-race"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    state=TaskState.EXEC_APPROVED,
                    proposed_by="agent-1",
                )
            )
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                current = await racer.scalar(select(Task).where(Task.id == task_id))
                assert current is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == current.version,  # type: ignore[arg-type]
                        Task.state == TaskState.READY.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        cancel_sources: list[str] = []

        async def _cancel(source: str) -> dict[str, object]:
            cancel_sources.append(source)
            return {}

        async def _approval_cancel(_run_id: str) -> dict[str, object]:
            return await _cancel("approval")

        async def _poller_cancel(_run_id: str) -> dict[str, object]:
            return await _cancel("poller")

        approval_executor = AsyncMock(spec=MacroAgentExecutor)
        approval_executor.start.side_effect = _start_then_lose
        approval_executor.cancel.side_effect = _approval_cancel
        poller_executor = AsyncMock(spec=MacroAgentExecutor)
        poller_executor.cancel.side_effect = _poller_cancel

        original_transition = StateMachine.atomic_transition

        async def _lose_running_cas(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original_transition(db, task, target_state)

        monkeypatch.setattr(
            StateMachine,
            "atomic_transition",
            staticmethod(_lose_running_cas),
        )

        cleanup_paused = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleanup_seen = False

        async with local_session() as db:
            original_execute = db.execute

            async def _execute(*args: object, **kwargs: object):
                nonlocal cleanup_seen
                statement = str(args[0]).lower() if args else ""
                if not cleanup_seen and "from execution" in statement:
                    cleanup_seen = True
                    cleanup_paused.set()
                    await release_cleanup.wait()
                return await original_execute(*args, **kwargs)

            monkeypatch.setattr(db, "execute", _execute)
            operation = asyncio.create_task(
                ApprovalService(
                    db=db,
                    executor=approval_executor,
                )._trigger_execution(
                    task=await db.scalar(select(Task).where(Task.id == task_id)),
                    contract=contract,
                    profile=profile,
                    actor="system",
                    source="test",
                    previous_state=TaskState.EXEC_APPROVED,
                )
            )
            await asyncio.wait_for(cleanup_paused.wait(), timeout=5)

            async with local_session() as poller_db:
                actions = await StuckExecutionPoller(
                    poller_db,
                    executor=poller_executor,
                )._poll_pending_cancellations()

            release_cleanup.set()
            with pytest.raises(ValueError, match="Concurrent modification"):
                await asyncio.wait_for(operation, timeout=5)

        assert actions[0]["action"] == "execution_cancel_completed"
        assert cancel_sources == ["poller"]
        approval_executor.cancel.assert_not_awaited()
        poller_executor.cancel.assert_awaited_once_with(run_id)

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.latest_macro_agent_run_id == run_id
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.cancellation_pending is False
            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert (
                len(
                    [
                        row
                        for row in audits
                        if row.event_type == "execution_cancel_completed"
                    ]
                )
                    == 1
            )

    @pytest.mark.skipif(
        os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="targets SQLite writer-lock behavior",
    )
    async def test_sqlite_approval_cleanup_is_single_flight_with_poller(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SQLite must not let the poller duplicate an approval cleanup call."""
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        _engine, local_session = isolated_db
        task_id = "task-sqlite-approval-cleanup-single-flight"
        run_id = "run-sqlite-approval-cleanup-single-flight"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    state=TaskState.EXEC_APPROVED,
                    proposed_by="agent-1",
                )
            )
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()
        approval_cancel_started = asyncio.Event()
        poller_cancel_started = asyncio.Event()
        release_approval_cancel = asyncio.Event()
        cancel_sources: list[str] = []

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                current = await racer.scalar(select(Task).where(Task.id == task_id))
                assert current is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == current.version,  # type: ignore[arg-type]
                        Task.state == TaskState.READY.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        async def _approval_cancel(_run_id: str) -> dict[str, object]:
            cancel_sources.append("approval")
            approval_cancel_started.set()
            await release_approval_cancel.wait()
            return {}

        async def _poller_cancel(_run_id: str) -> dict[str, object]:
            cancel_sources.append("poller")
            poller_cancel_started.set()
            return {}

        approval_executor = AsyncMock(spec=MacroAgentExecutor)
        approval_executor.start.side_effect = _start_then_lose
        approval_executor.cancel.side_effect = _approval_cancel
        poller_executor = AsyncMock(spec=MacroAgentExecutor)
        poller_executor.cancel.side_effect = _poller_cancel

        original_transition = StateMachine.atomic_transition

        async def _lose_running_cas(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original_transition(db, task, target_state)

        monkeypatch.setattr(
            StateMachine,
            "atomic_transition",
            staticmethod(_lose_running_cas),
        )

        async with local_session() as approval_db:
            task = await approval_db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            approval_operation = asyncio.create_task(
                ApprovalService(
                    db=approval_db,
                    executor=approval_executor,
                )._trigger_execution(
                    task=task,
                    contract=contract,
                    profile=profile,
                    actor="system",
                    source="test",
                    previous_state=TaskState.EXEC_APPROVED,
                )
            )
            await asyncio.wait_for(approval_cancel_started.wait(), timeout=5)

            async with local_session() as poller_db:
                poller_operation = asyncio.create_task(
                    StuckExecutionPoller(
                        poller_db,
                        executor=poller_executor,
                    )._poll_pending_cancellations()
                )
                # With the fix, BEGIN IMMEDIATE blocks until approval commits.
                with suppress(TimeoutError):
                    await asyncio.wait_for(poller_cancel_started.wait(), timeout=1)

                release_approval_cancel.set()
                with pytest.raises(ValueError, match="Concurrent modification"):
                    await asyncio.wait_for(approval_operation, timeout=5)
                actions = await asyncio.wait_for(poller_operation, timeout=5)

        assert cancel_sources == ["approval"]
        approval_executor.cancel.assert_awaited_once_with(run_id)
        poller_executor.cancel.assert_not_awaited()
        assert actions == []

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_running_cas_loss_records_and_cancels_started_run(
        self,
        isolated_db: tuple,
    ) -> None:
        """#293: a run returned before CAS loss must not become untracked."""
        _engine, local_session = isolated_db
        task_id = "task-orphaned-start-293"

        async with local_session() as seed:
            await _seed_task(seed, task_id)

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)

        async def _start_then_win_elsewhere(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.FAILED
                )
                assert won is True
                await racer.commit()
            return {"run_id": "run-orphan-293"}

        fake_executor.start.side_effect = _start_then_win_elsewhere
        fake_executor.cancel.return_value = {}

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await ApprovalService(db=db, executor=fake_executor).approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-orphaned-start-293",
                )

        fake_executor.cancel.assert_awaited_once_with("run-orphan-293")

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED

            executions = await check.execute(
                select(Execution).where(Execution.task_id == task_id)
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None
            assert rows[0].macro_agent_run_id == "run-orphan-293"
            assert rows[0].cancellation_pending is False

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
            concurrent = [
                row
                for row in audits.scalars().all()
                if row.event_type == "concurrent_modification"
            ]
            assert concurrent
            assert concurrent[-1].payload["macro_agent_run_id"] == "run-orphan-293"

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_running_cas_loss_crash_after_run_id_commit_leaves_cancel_pending(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#293: a crash cannot leave a started run outside cancellation recovery."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        _engine, local_session = isolated_db
        task_id = "task-orphaned-start-crash-293"

        async with local_session() as seed:
            await _seed_task(seed, task_id)

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.FAILED
                )
                assert won is True
                await racer.commit()
            return {"run_id": "run-orphan-crash-293"}

        fake_executor.start.side_effect = _start_then_lose

        async with local_session() as db:
            real_commit = db.commit
            commit_count = 0

            async def _commit_then_crash() -> None:
                nonlocal commit_count
                await real_commit()
                commit_count += 1
                # The pre-fix third commit contains only macro_agent_run_id.
                if commit_count == 3:
                    raise asyncio.CancelledError

            monkeypatch.setattr(db, "commit", _commit_then_crash)
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(asyncio.CancelledError):
                await ApprovalService(db=db, executor=fake_executor).approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-orphan-crash-293",
                )
            assert commit_count == 3

        async with local_session() as check:
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.macro_agent_run_id == "run-orphan-crash-293"
            assert execution.cancellation_pending is True

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "execution_cancel_pending"
                and row.payload["macro_agent_run_id"] == "run-orphan-crash-293"
                for row in audits
            )

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_recover_orphaned_run_cas_loss_queues_cancellation(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#359: a run recovered by lookup must not become untracked when the
        recovery path's own CAS to RUNNING loses to a concurrent writer."""
        import httpx

        from governance_controller import config
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        _engine, local_session = isolated_db
        task_id = "task-recover-orphan-cas-loss-359"
        run_id = "run-recovered-cas-loss-359"
        prior_key = "exec-recover-cas-loss-359"

        async with local_session() as seed:
            await _seed_task(seed, task_id)
            seeded = await seed.scalar(select(Task).where(Task.id == task_id))
            assert seeded is not None
            seeded.macro_agent_idempotency_key = prior_key
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = httpx.ReadTimeout(
            "lost response", request=httpx.Request("POST", "http://macro.example/runs")
        )

        async def _lookup_then_lose_elsewhere(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            # The recovery path confirms the run is live via lookup() before
            # attempting its own CAS to RUNNING. Race a concurrent writer (the
            # poller's own READY-timeout sweep, in production) into moving the
            # task away from READY first, so the recovery CAS loses.
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.FAILED
                )
                assert won is True
                await racer.commit()
            return {"run_id": run_id, "status": "queued"}

        fake_executor.lookup.side_effect = _lookup_then_lose_elsewhere

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(RuntimeError, match="macro-agent start failed"):
                await ApprovalService(db=db, executor=fake_executor).approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key="key-recover-cas-loss-359",
                )

        fake_executor.lookup.assert_awaited_once_with(prior_key)

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED

            executions = await check.execute(
                select(Execution).where(Execution.task_id == task_id)
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None
            assert rows[0].macro_agent_run_id == run_id
            # The core regression: without the fix this stays False and the
            # run is permanently invisible to _poll_pending_cancellations.
            assert rows[0].cancellation_pending is True

            audits = (
                await check.execute(
                    select(AuditLog).where(AuditLog.task_id == task_id)
                )
            ).scalars().all()
            assert any(
                row.event_type == "execution_cancel_pending"
                and row.payload["macro_agent_run_id"] == run_id
                and row.payload["reason"] == "execution_start_recovery_cas_lost"
                for row in audits
            )

        # The run must now be durably queued for the existing cleanup path:
        # the poller should discover and cancel it.
        poller_executor = AsyncMock(spec=MacroAgentExecutor)
        poller_executor.cancel.return_value = {}
        async with local_session() as poller_db:
            actions = await StuckExecutionPoller(
                poller_db,
                executor=poller_executor,
            )._poll_pending_cancellations()

        poller_executor.cancel.assert_awaited_once_with(run_id)
        assert any(
            action["action"] == "execution_cancel_completed"
            and action["macro_agent_run_id"] == run_id
            for action in actions
        )

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_cancellation_cleanup_locks_execution_before_task(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cleanup must acquire Execution then Task to match the poller order."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        engine, local_session = isolated_db
        task_id = "task-cancel-lock-order"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    state=TaskState.EXEC_APPROVED,
                    proposed_by="agent-1",
                )
            )
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-lock-order"}
        fake_executor.cancel.return_value = {}

        original_transition = StateMachine.atomic_transition

        async def _lose_running_cas(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original_transition(db, task, target_state)

        monkeypatch.setattr(
            StateMachine,
            "atomic_transition",
            staticmethod(_lose_running_cas),
        )

        lock_queries: list[str] = []

        def _capture_lock_query(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if "for update" not in normalized:
                return
            if "from execution" in normalized or "from task" in normalized:
                lock_queries.append(normalized)

        event.listen(engine.sync_engine, "before_cursor_execute", _capture_lock_query)
        try:
            async with local_session() as db:
                task = await db.scalar(select(Task).where(Task.id == task_id))
                assert task is not None
                with pytest.raises(ValueError, match="Concurrent modification"):
                    await ApprovalService(
                        db=db, executor=fake_executor
                    )._trigger_execution(
                        task=task,
                        contract=contract,
                        profile=profile,
                        actor="system",
                        source="test",
                        previous_state=TaskState.EXEC_APPROVED,
                    )
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", _capture_lock_query
            )

        execution_lock = next(
            index
            for index, query in enumerate(lock_queries)
            if "from execution" in query
        )
        task_lock = next(
            index for index, query in enumerate(lock_queries) if "from task" in query
        )
        assert execution_lock < task_lock

    async def test_failed_cas_cleanup_leaves_pending_cancellation(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed orphan cleanup must be durable for the poller to retry."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        _engine, local_session = isolated_db
        task_id = "task-cancel-pending"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.EXEC_APPROVED,
                )
            )
            await seed.commit()

        contract = _make_contract(task_id)
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-cancel-pending"}
        fake_executor.cancel.side_effect = RuntimeError("macro-agent unavailable")

        original = StateMachine.atomic_transition

        async def _lose_running_cas(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original(db, task, target_state)

        monkeypatch.setattr(
            StateMachine, "atomic_transition", staticmethod(_lose_running_cas)
        )

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await ApprovalService(db=db, executor=fake_executor)._trigger_execution(
                    task=task,
                    contract=contract,
                    profile=profile,
                    actor="system",
                    source="test",
                    previous_state=TaskState.EXEC_APPROVED,
                )

        async with local_session() as check:
            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "execution_cancel_pending" for row in audits
            )

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.cancellation_pending is True
