"""Tests for triggering macro-agent execution after EXECUTION approval."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PLAN_APPROVED,
    task_id: str = "task-exec",
) -> Task:
    task = Task(
        id=task_id, project_id="proj-1", state=state, proposed_by="agent-1"
    )
    db.add(task)
    await db.flush()
    return task


def _make_contract(
    objective: str = "Implement feature X",
    acceptance: list[str] | None = None,
    harness: str = "opencode",
) -> TaskContract:
    data: dict = {
        "task_id": "task-exec",
        "project_id": "proj-1",
        "proposed_by": "agent-1",
        "objective": objective,
        "execution": ExecutionConfig(harness=harness),
        "forbidden_paths": [],
    }
    if acceptance is not None:
        data["acceptance"] = acceptance
    else:
        data["acceptance"] = ["feature X passes tests"]
    return TaskContract(**data)


def _make_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": ["opencode"]},
        security={"forbidden_paths": []},
    )


class TestExecutionTrigger:
    async def test_execution_approval_starts_macro_agent_and_creates_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-abc-123"}
        service = ApprovalService(db=db_session, executor=fake_executor)

        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="telegram",
            actor="admin",
            idempotency_key="key-exec-trigger",
        )

        assert result.state == TaskState.RUNNING
        fake_executor.start.assert_awaited_once_with(contract)

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-abc-123"
        assert execution.state == TaskState.RUNNING

    async def test_execution_start_failure_transitions_task_to_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = RuntimeError("macro-agent unavailable")
        service = ApprovalService(db=db_session, executor=fake_executor)

        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        with pytest.raises(RuntimeError, match="macro-agent start failed"):
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="telegram",
            actor="admin",
            idempotency_key="key-exec-fail",
        )

        assert task.state == TaskState.FAILED

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is None

    async def test_plan_approval_does_not_create_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        service = ApprovalService(db=db_session, executor=fake_executor)

        task = await _make_task(db_session, TaskState.PROPOSED)
        contract = _make_contract()
        profile = _make_profile()

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="key-plan-noexec",
        )

        assert result.state == TaskState.PLAN_APPROVED
        fake_executor.start.assert_not_awaited()

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is None
