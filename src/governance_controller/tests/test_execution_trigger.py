"""Tests for triggering macro-agent execution after EXECUTION approval."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.opentasks import OpentasksDAG
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.permission_service import PermissionService


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PLAN_APPROVED,
    task_id: str = "task-exec",
) -> Task:
    task = Task(id=task_id, project_id="proj-1", state=state, proposed_by="agent-1")
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
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#301: a lost macro-agent start response cannot be correlated "
            "without macro-agent idempotency or lookup"
        ),
    )
    async def test_accepted_start_is_durably_attached_before_process_loss(
        self,
        isolated_db: tuple,
        patched_db,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Expose the response-loss window between start and Controller commit."""
        from contextlib import asynccontextmanager

        from governance_controller.services.state_machine import StateMachine

        _engine, local_session = isolated_db
        task_id = "task-start-response-loss"
        accepted_run_id = "run-accepted-before-loss"

        async with local_session() as seed:
            await _make_task(seed, TaskState.PLAN_APPROVED, task_id)
            await seed.commit()

        contract = _make_contract()
        contract = contract.model_copy(update={"task_id": task_id})
        profile = _make_profile()
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": accepted_run_id}

        original_transition = StateMachine.atomic_transition

        async def _crash_before_running_commit(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                raise asyncio.CancelledError
            return await original_transition(db, task, target_state)

        monkeypatch.setattr(
            StateMachine,
            "atomic_transition",
            staticmethod(_crash_before_running_commit),
        )

        service = ApprovalService(
            db=None,  # type: ignore[arg-type]
            executor=fake_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )
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
                    idempotency_key="key-start-response-loss",
                )

        async with local_session() as check:
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.macro_agent_run_id == accepted_run_id

    async def test_execution_materialization_uses_plane_issue_id(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#282: approved execution materializes from Plane's issue UUID."""
        from governance_controller import config
        from governance_controller.services import approval_service as module

        monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
        materializer = AsyncMock()
        materializer.materialize.return_value = OpentasksDAG(project_id="proj-1")
        monkeypatch.setattr(
            module,
            "OpentasksMaterializer",
            lambda: materializer,
        )
        projection = AsyncMock()
        projection.update_state.return_value = {}
        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.return_value = {"run_id": "run-plane-id"}

        service = ApprovalService(
            db=db_session,
            executor=executor,
            plane_projection=projection,
            permission_service=PermissionService(human_approval_verified=True),
        )
        task = await _make_task(db_session, TaskState.PLAN_APPROVED, "controller-task")
        task.plane_issue_id = "plane-issue-uuid"

        await service.approve(
            task=task,
            contract=_make_contract(),
            profile=_make_profile(),
            approval_type=ApprovalType.EXECUTION,
            source="cli",
            actor="admin",
            idempotency_key="key-plane-id",
        )

        materializer.materialize.assert_awaited_once_with(
            root_plane_task_id="plane-issue-uuid",
            project_id="proj-1",
        )

    async def test_execution_approval_starts_macro_agent_and_creates_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-abc-123"}
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )

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
        fake_executor.start.assert_awaited_once()

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
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )

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
        assert execution is not None
        assert execution.state == TaskState.FAILED
        assert execution.ended_at is not None

    async def test_materialization_failure_transitions_task_to_failed(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#384: a MaterializerError must fail the task immediately, the same
        way an executor.start() failure does, instead of leaving it stuck at
        READY with no user-facing retry path."""
        from governance_controller import config
        from governance_controller.services import approval_service as module
        from governance_controller.services.opentasks_materializer import (
            MaterializerError,
        )

        monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
        materializer = AsyncMock()
        materializer.materialize.side_effect = MaterializerError(
            "dependency cycle detected"
        )
        monkeypatch.setattr(
            module,
            "OpentasksMaterializer",
            lambda: materializer,
        )

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )

        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        with pytest.raises(RuntimeError, match="Failed to materialize opentasks DAG"):
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="telegram",
                actor="admin",
                idempotency_key="key-materialize-fail",
            )

        fake_executor.start.assert_not_awaited()
        assert task.state == TaskState.FAILED

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.state == TaskState.FAILED
        assert execution.ended_at is not None

    async def test_plan_approval_does_not_create_execution(
        self,
        db_session: AsyncSession,
    ) -> None:
        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )

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
