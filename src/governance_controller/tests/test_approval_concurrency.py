"""Concurrency regression tests for approval paths.

SQLite (the default test database) does not reliably exercise true
interleaved concurrent writes with SQLAlchemy's async SQLite driver, so the
core guard is verified deterministically: a second session that reads the
task before the first session commits, then tries to approve after the first
session has already advanced the task, observes ``rowcount == 0`` and fails
with ``Concurrent modification detected``.
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService


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


def _file_db_session_maker():
    """Return a fresh engine and sessionmaker for a new file-backed SQLite DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_url = f"sqlite+aiosqlite:///{tmp.name}"
    engine = create_async_engine(db_url, echo=False, future=True)
    local_session = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, local_session, tmp.name


class TestApprovalConcurrency:
    async def test_second_approval_after_commit_fails_with_concurrent_modification(
        self,
    ) -> None:
        """A stale read followed by a committed advance must be rejected.

        This is the deterministic equivalent of the race: session A reads the
        task, session B commits an approval that advances the task, and then
        session A's approval sees rowcount == 0 and raises.
        """
        engine, local_session, path = _file_db_session_maker()

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        async with local_session() as seed:
            await _seed_task(seed, "task-stale")

        contract = _make_contract("task-stale")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-first"}

        # Session A reads the task but does not commit yet.
        session_a = local_session()
        task_a = await session_a.scalar(
            select(Task).where(Task.id == "task-stale")
        )
        assert task_a is not None
        assert task_a.state == TaskState.PLAN_APPROVED
        assert task_a.version == 0

        # Session B approves and commits first.
        async with local_session() as session_b:
            service_b = ApprovalService(db=session_b, executor=fake_executor)
            task_b = await session_b.scalar(
                select(Task).where(Task.id == "task-stale")
            )
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
            # Successful EXECUTION approval performs 5 CAS increments:
            # PLAN_APPROVED -> EXEC_APPROVED, EXEC_APPROVED -> READY,
            # READY -> RUNNING (logged as state_change), plus the executor
            # state updates and final READY -> RUNNING atomic_transition.
            # The task starts at version 0, so the final version is 5.
            assert task.version == 5

        await engine.dispose()
        os.unlink(path)

    async def test_executor_failure_advances_to_failed_with_version_5(
        self,
    ) -> None:
        """If executor.start raises, the task ends at FAILED with version 5.

        The CAS increments are PLAN_APPROVED -> EXEC_APPROVED,
        EXEC_APPROVED -> READY, READY -> FAILED. Additional internal
        state-change updates (e.g. execution row state) add two more version
        bumps, so the final version is 5.
        """
        engine, local_session, path = _file_db_session_maker()

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        async with local_session() as seed:
            await _seed_task(seed, "task-exec-fail")

        contract = _make_contract("task-exec-fail")
        profile = _make_profile()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = RuntimeError("boom")

        async with local_session() as db:
            service = ApprovalService(db=db, executor=fake_executor)
            task = await db.scalar(
                select(Task).where(Task.id == "task-exec-fail")
            )
            assert task is not None
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
            await db.commit()

        async with local_session() as check:
            task = await check.scalar(
                select(Task).where(Task.id == "task-exec-fail")
            )
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.version == 5

            executions = await check.execute(
                select(Execution).where(Execution.task_id == "task-exec-fail")
            )
            assert len(executions.scalars().all()) == 1

        await engine.dispose()
        os.unlink(path)

    async def test_concurrent_execution_approvals_do_not_double_trigger(
        self,
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
        engine, local_session, path = _file_db_session_maker()

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

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
        assert "Concurrent modification detected" in str(errors[0])
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

            task = await check.scalar(
                select(Task).where(Task.id == "task-concurrent")
            )
            assert task is not None
            assert task.state == TaskState.RUNNING

        await engine.dispose()
        os.unlink(path)
