"""Concurrency regression tests for approval paths."""

import asyncio
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PLAN_APPROVED,
    task_id: str = "task-concurrent",
) -> Task:
    task = Task(
        id=task_id,
        project_id="proj-1",
        state=state,
        proposed_by="agent-1",
    )
    db.add(task)
    await db.flush()
    return task


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


class TestApprovalConcurrency:
    async def test_concurrent_execution_approvals_do_not_double_trigger(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Two concurrent EXECUTION approvals with distinct idempotency keys
        must not both call MacroAgentExecutor.start().

        The first caller acquires the row lock, advances state, and starts the
        macro-agent. The second caller must observe a state transition conflict
        and fail without calling executor.start() again.
        """
        contract = _make_contract()
        profile = _make_profile()
        # Pre-create the task in the database so concurrent sessions can lock it.
        await _make_task(db_session, TaskState.PLAN_APPROVED)

        start_counts = []
        errors: list[Exception] = []

        async def _approve_with_session(
            idempotency_key: str,
        ) -> Task:
            fake_executor = AsyncMock(spec=MacroAgentExecutor)
            fake_executor.start.return_value = {"run_id": f"run-{idempotency_key}"}

            async with db_session.begin_nested():
                service = ApprovalService(db=db_session, executor=fake_executor)
                task = await db_session.scalar(
                    select(Task).where(Task.id == "task-concurrent")
                )
                assert task is not None
                result = await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="test",
                    actor="admin",
                    idempotency_key=idempotency_key,
                )
                start_counts.append(
                    fake_executor.start.await_count
                )
                return result

        coroutines = [
            _approve_with_session("key-concurrent-1"),
            _approve_with_session("key-concurrent-2"),
        ]

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        errors.extend(failures)

        # Exactly one approval may succeed; the other must fail on transition.
        assert len(successes) == 1
        assert len(errors) == 1
        assert "Invalid transition" in str(errors[0]) or "concurrent" in str(
            errors[0]
        ).lower()

        # executor.start() must have been awaited exactly once across both calls.
        assert sum(start_counts) == 1

        # Only one Execution row and one Approval row should exist.
        executions = await db_session.execute(
            select(Execution).where(Execution.task_id == "task-concurrent")
        )
        approvals = await db_session.execute(
            select(Approval).where(Approval.task_id == "task-concurrent")
        )
        assert len(executions.scalars().all()) == 1
        assert len(approvals.scalars().all()) == 1

        # The task ended in RUNNING.
        task = await db_session.scalar(
            select(Task).where(Task.id == "task-concurrent")
        )
        assert task is not None
        assert task.state == TaskState.RUNNING
