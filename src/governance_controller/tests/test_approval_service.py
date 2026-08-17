"""Tests for the ApprovalService."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.policy_engine import PolicyEngine


def _make_task(state: TaskState = TaskState.PROPOSED) -> Task:
    return Task(id="task-1", project_id="proj-1", state=state)


def _make_contract(
    objective: str = "Implement feature X",
    acceptance: list[str] | None = None,
    harness: str = "opencode",
    forbidden_paths: list[str] | None = None,
) -> TaskContract:
    data: dict = {
        "task_id": "task-1",
        "project_id": "proj-1",
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
def service(db_session: AsyncSession) -> ApprovalService:
    return ApprovalService(db=db_session)


class TestApprovalServiceStateTransitions:
    async def test_plan_approval_advances_state_to_plan_approved(
        self,
        service: ApprovalService,
    ) -> None:
        task = _make_task(TaskState.PROPOSED)
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

    async def test_execution_approval_advances_state_to_exec_approved(
        self,
        service: ApprovalService,
    ) -> None:
        task = _make_task(TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="telegram",
            actor="human-1",
            idempotency_key="key-exec-1",
        )

        assert result.state == TaskState.EXEC_APPROVED

    async def test_merge_approval_advances_state_to_done(
        self,
        service: ApprovalService,
    ) -> None:
        task = _make_task(TaskState.HUMAN_REVIEW)
        contract = _make_contract()
        profile = _make_profile()

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.MERGE,
            source="dashboard",
            actor="human-1",
            idempotency_key="key-merge-1",
        )

        assert result.state == TaskState.DONE


class TestApprovalServiceIdempotency:
    async def test_idempotent_second_approval_returns_same_state(
        self,
        service: ApprovalService,
    ) -> None:
        task = _make_task(TaskState.PROPOSED)
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

        second = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="telegram",
            actor="human-1",
            idempotency_key="key-plan-2",
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
    ) -> None:
        task = _make_task(TaskState.PLAN_APPROVED)
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
        task = _make_task(TaskState.PROPOSED)

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
