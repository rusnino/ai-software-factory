"""Concurrency regression tests for approval paths.

The tests use the ``isolated_db`` fixture so they run identically against the
configured test database (SQLite by default, PostgreSQL via
``GC_TEST_DATABASE_URL``). The deterministic CAS-loss scenarios exercise the
production guard without requiring true interleaved concurrency.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
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
        both the task and the Execution row at ``READY`` (so a real macro-agent
        run is not orphaned as RUNNING for a FAILED/READY task) and preserve
        the ``concurrent_modification`` audit row (#262).
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
            assert rows[0].state == TaskState.READY
            assert rows[0].macro_agent_run_id is None
