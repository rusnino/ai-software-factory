"""Tests for the ApprovalService."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.policy_engine import PolicyEngine


@pytest.fixture
def fake_executor() -> MacroAgentExecutor:
    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.return_value = {"run_id": "run-test-1"}
    return executor


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PROPOSED,
    task_id: str = "task-1",
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
    forbidden_paths: list[str] | None = None,
) -> TaskContract:
    data: dict = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "proposed_by": "agent-1",
        "objective": objective,
        "execution": ExecutionConfig(harness=harness),
        "forbidden_paths": forbidden_paths or [],
    }
    if acceptance is not None:
        data["acceptance"] = acceptance
    else:
        data["acceptance"] = ["feature X passes tests"]
    return TaskContract(**data)


def _make_profile(
    allowed_harnesses: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": allowed_harnesses or ["opencode"]},
        security={"forbidden_paths": forbidden_paths or []},
    )


@pytest.fixture
def service(
    db_session: AsyncSession,
    fake_executor: MacroAgentExecutor,
) -> ApprovalService:
    return ApprovalService(db=db_session, executor=fake_executor)


class TestApprovalServiceStateTransitions:
    async def test_plan_approval_advances_state_to_plan_approved(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
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
            idempotency_key="key-plan-1",
        )

        assert result.state == TaskState.PLAN_APPROVED

    async def test_execution_approval_starts_run_and_advances_to_running(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
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
            idempotency_key="key-exec-1",
        )

        assert result.state == TaskState.RUNNING
        fake_executor.start.assert_awaited_once()

    async def test_merge_approval_advances_state_to_done(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        task = await _make_task(db_session, TaskState.HUMAN_REVIEW)
        contract = _make_contract()
        profile = _make_profile()

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.MERGE,
            source="dashboard",
            actor="admin",
            idempotency_key="key-merge-1",
        )

        assert result.state == TaskState.DONE


class TestApprovalServiceIdempotency:
    async def test_idempotent_second_approval_returns_same_state(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        task = await _make_task(db_session, TaskState.PROPOSED)
        contract = _make_contract()
        profile = _make_profile()

        first = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="key-plan-1",
        )
        assert first.state == TaskState.PLAN_APPROVED

        # Same idempotency key: must be a no-op and not create a second row.
        second = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="telegram",
            actor="human-1",
            idempotency_key="key-plan-1",
        )

        assert second.state == TaskState.PLAN_APPROVED

        approvals = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == task.id)
        )
        assert len(approvals.scalars().all()) == 1



class TestApprovalServicePolicyViolations:
    async def test_policy_violation_raises_value_error(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract(harness="forbidden-harness")
        profile = _make_profile()

        with pytest.raises(ValueError, match="Policy violation"):
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="human-1",
                idempotency_key="key-exec-bad",
            )

        assert task.state == TaskState.PLAN_APPROVED

    async def test_policy_engine_injected_used(
        self,
        db_session: AsyncSession,
    ) -> None:
        class AlwaysDeny(PolicyEngine):
            @classmethod
            def evaluate(cls, contract, profile, approval_type):
                return type(
                    "PolicyResult",
                    (),
                    {"allowed": False, "violations": ["injected-deny"]},
                )()

        service = ApprovalService(db=db_session, policy_engine=AlwaysDeny)
        task = await _make_task(db_session, TaskState.PROPOSED)

        with pytest.raises(ValueError, match="injected-deny"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="human-1",
                idempotency_key="key-injected",
            )


class TestApprovalServiceSelfApprovalPrevention:
    async def test_proposer_cannot_approve_own_task(
        self,
        db_session: AsyncSession,
    ) -> None:
        service = ApprovalService(db=db_session)
        task = await _make_task(db_session, TaskState.PROPOSED)
        # Simulate the proposer attempting to approve their own task.
        with pytest.raises(ValueError, match="cannot approve their own task"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="agent-1",
                idempotency_key="key-self-approve",
            )

    async def test_system_actor_cannot_approve(
        self,
        db_session: AsyncSession,
    ) -> None:
        service = ApprovalService(db=db_session)
        task = await _make_task(db_session, TaskState.PROPOSED)
        for forbidden_actor in ("system", "agent"):
            with pytest.raises(ValueError, match="may not request"):
                await service.approve(
                    task=task,
                    contract=_make_contract(),
                    profile=_make_profile(),
                    approval_type=ApprovalType.PLAN,
                    source="plane",
                    actor=forbidden_actor,
                    idempotency_key=f"key-{forbidden_actor}",
                )
