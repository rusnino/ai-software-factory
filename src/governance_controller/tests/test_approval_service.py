"""Tests for the ApprovalService."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.client import MacroAgentResponseError
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.permission_service import PermissionService
from governance_controller.services.policy_engine import (
    PolicyEngine,
    PolicyViolationError,
)


class _FakePlaneProjection:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update_state(
        self,
        controller_task_id: str,
        plane_issue_id: str,
        state: Any,
        project_id: str | None = None,
        opentasks_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "controller_task_id": controller_task_id,
                "plane_issue_id": plane_issue_id,
                "state": state,
                "project_id": project_id,
                "opentasks_id": opentasks_id,
            }
        )
        return {"id": plane_issue_id}


async def _audit_rows_for_task(
    db_session: AsyncSession, task_id: str
) -> list[AuditLog]:
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.task_id == task_id)
    )
    return list(result.scalars().all())


@pytest.fixture
def fake_executor() -> MacroAgentExecutor:
    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.return_value = {"run_id": "run-1", "status": "queued"}
    executor.lookup.return_value = None
    return executor


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PROPOSED,
    task_id: str = "task-1",
) -> Task:
    task = Task(id=task_id, project_id="proj-1", state=state, proposed_by="agent-1")
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
    return ApprovalService(
        db=db_session,
        executor=fake_executor,
        permission_service=PermissionService(
            admins={"admin"}, human_approval_verified=True
        ),
    )


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

    async def test_execution_approval_rejects_terminal_macro_agent_status(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#349: a dedup hit returning a terminal run must not advance to RUNNING."""
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()
        fake_executor.start.return_value = {
            "run_id": "run-terminal-349",
            "status": "done",
        }

        with pytest.raises(RuntimeError, match="terminal status"):
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="telegram",
                actor="admin",
                idempotency_key="key-terminal-349",
            )

        await db_session.refresh(task)
        assert task.state == TaskState.FAILED

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.state == TaskState.FAILED.value
        assert execution.macro_agent_run_id == "run-terminal-349"

        audit = await db_session.execute(
            select(AuditLog).where(
                AuditLog.task_id == task.id,
                AuditLog.event_type == "execution_start_failed",
            )
        )
        assert len(audit.scalars().all()) == 1

    async def test_execution_approval_ready_audit_previous_state_is_exec_approved(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#241: READY transition audit entry must use EXEC_APPROVED."""
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="telegram",
            actor="admin",
            idempotency_key="key-exec-audit-241",
        )

        entries = await _audit_rows_for_task(db_session, task.id)
        ready_changes = [
            e
            for e in entries
            if e.event_type == "state_change"
            and e.payload.get("new_state") == TaskState.READY.value
        ]
        assert len(ready_changes) == 1
        previous = ready_changes[0].payload.get("previous_state")
        assert previous == TaskState.EXEC_APPROVED.value

    async def test_execution_start_orphan_failure_preserves_idempotency_key(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#301: a lost response keeps the execution id as a dedup key for retry."""
        import httpx

        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()
        fake_executor.start.side_effect = httpx.ReadTimeout(
            "lost response", request=httpx.Request("POST", "http://macro.example/runs")
        )

        with pytest.raises(RuntimeError, match="macro-agent start failed"):
            await service.approve(
                task=task,
                contract=contract,
                profile=profile,
                approval_type=ApprovalType.EXECUTION,
                source="telegram",
                actor="admin",
                idempotency_key="key-orphan-301",
            )

        await db_session.refresh(task)
        assert task.macro_agent_idempotency_key is not None
        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert task.macro_agent_idempotency_key == execution.id

    async def test_execution_start_recovers_orphaned_run_by_lookup(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#301: a lost start response recovers the existing run by lookup."""
        import httpx

        prior_key = "exec-orphan-301"
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        task.macro_agent_idempotency_key = prior_key
        contract = _make_contract()
        profile = _make_profile()
        fake_executor.start.side_effect = httpx.ReadTimeout(
            "lost response", request=httpx.Request("POST", "http://macro.example/runs")
        )
        fake_executor.lookup.return_value = {
            "run_id": "run-recovered-301",
            "status": "queued",
        }

        result = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="telegram",
            actor="admin",
            idempotency_key="key-retry-301",
        )

        assert result.state == TaskState.RUNNING
        fake_executor.lookup.assert_awaited_once_with(prior_key)

        execution = await db_session.scalar(
            select(Execution).where(Execution.task_id == task.id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-recovered-301"

    async def test_orphan_recovery_cas_loss_marks_live_run_for_cleanup(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#359: a lookup hit that then loses the RUNNING CAS to a concurrent
        writer must not silently drop the genuinely live run it just found.
        """
        task = await _make_task(db_session, TaskState.READY, task_id="task-359")
        execution = Execution(
            id="exec-359",
            task_id=task.id,
            state=TaskState.READY,
            started_at=datetime.now(UTC),
        )
        db_session.add(execution)
        await db_session.flush()

        fake_executor.lookup.return_value = {
            "run_id": "run-cas-loss-359",
            "status": "queued",
        }

        # Simulate a concurrent writer (e.g. a cancellation or the poller's own
        # timeout sweep) moving the task away from READY without updating this
        # in-memory `task` object, so the CAS below observes a stale version.
        await db_session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(state=TaskState.FAILED.value, version=Task.version + 1)
            .execution_options(synchronize_session=False)
        )
        await db_session.commit()

        recovered = await service._try_recover_orphaned_run(
            task=task,
            execution=execution,
            controller_execution_id="key-cas-loss-359",
            actor="system",
            source="stuck_execution_poller",
        )

        assert recovered is False

        refreshed_execution = await db_session.scalar(
            select(Execution).where(Execution.id == execution.id)
        )
        assert refreshed_execution is not None
        assert refreshed_execution.cancellation_pending is True
        assert refreshed_execution.macro_agent_run_id == "run-cas-loss-359"

        audit_rows = await _audit_rows_for_task(db_session, task.id)
        assert any(
            row.event_type == "execution_cancel_pending"
            and row.payload.get("reason") == "execution_start_recovery_cas_lost"
            and row.payload.get("macro_agent_run_id") == "run-cas-loss-359"
            and row.payload.get("controller_execution_id") == "key-cas-loss-359"
            for row in audit_rows
        )
        assert any(
            row.event_type == "concurrent_modification" for row in audit_rows
        )

    async def test_execution_start_failure_classifies_orphan_risk(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """#301: audit payload classifies macro-agent start failures by orphan risk."""
        contract = _make_contract()
        profile = _make_profile()
        request = httpx.Request("POST", "http://macro.example/runs")
        response = httpx.Response(500, request=request)

        cases = [
            (httpx.ConnectError("no route", request=request), "never_sent"),
            (httpx.ConnectTimeout("timed out", request=request), "never_sent"),
            (httpx.ReadTimeout("timed out", request=request), "orphan_suspected"),
            (httpx.WriteTimeout("timed out", request=request), "orphan_suspected"),
            (httpx.PoolTimeout("no pool", request=request), "orphan_suspected"),
            (
                httpx.HTTPStatusError("bad", request=request, response=response),
                "rejected",
            ),
            (MacroAgentResponseError("invalid json"), "accepted_response_invalid"),
        ]

        for index, (exc, expected_class) in enumerate(cases):
            task = await _make_task(
                db_session,
                TaskState.PLAN_APPROVED,
                task_id=f"task-fail-{expected_class}-{index}",
            )
            fake_executor.start.side_effect = exc
            with pytest.raises(RuntimeError, match="macro-agent start failed"):
                await service.approve(
                    task=task,
                    contract=contract,
                    profile=profile,
                    approval_type=ApprovalType.EXECUTION,
                    source="telegram",
                    actor="admin",
                    idempotency_key=f"key-exec-fail-{expected_class}",
                )

            rows = await _audit_rows_for_task(db_session, task.id)
            failure_rows = [
                r
                for r in rows
                if r.event_type == "execution_start_failed"
                and r.payload.get("failure_class") == expected_class
            ]
            assert len(failure_rows) == 1, (
                f"missing failure_class={expected_class} for {exc}"
            )

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

    async def test_approval_projects_state_to_plane(
        self,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        fake_projection = _FakePlaneProjection()
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            plane_projection=fake_projection,
            permission_service=PermissionService(admins={"admin"}),
        )
        task = await _make_task(db_session, TaskState.PROPOSED)
        contract = _make_contract()
        profile = _make_profile()

        await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="key-plan-projection",
        )

        assert len(fake_projection.calls) == 1
        assert fake_projection.calls[0]["state"] == TaskState.PLAN_APPROVED


async def test_plane_projection_crash_leaves_durable_pending_marker(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#298: a crash during Plane I/O leaves an auditable pending marker."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
    _engine, session_local = isolated_db
    creator = session_local()
    task = Task(
        id="task-plane-pending-298",
        project_id="project-plane-pending-298",
        state=TaskState.PLAN_APPROVED,
        proposed_by="agent-1",
        plane_issue_id="plane-issue-298",
    )
    creator.add(task)
    await creator.commit()

    class _CrashedProjection:
        async def update_state(self, **_kwargs: object) -> dict[str, object]:
            raise asyncio.CancelledError

    service = ApprovalService(
        db=creator,
        executor=AsyncMock(),
        plane_projection=_CrashedProjection(),  # type: ignore[arg-type]
        permission_service=PermissionService(admins={"admin"}),
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            await service._project_state_to_plane(
                task=task,
                state=TaskState.PLAN_APPROVED,
                approval_type=ApprovalType.PLAN,
            )
        await creator.rollback()

        async with session_local() as observer:
            rows = await observer.execute(
                select(AuditLog).where(
                    AuditLog.task_id == "task-plane-pending-298"
                )
            )
            entries = list(rows.scalars().all())

        pending = [
            entry
            for entry in entries
            if entry.event_type == "plane_projection_pending"
        ]
        assert len(pending) == 1
        assert pending[0].payload["operation"] == "update_state"
        assert pending[0].payload["plane_issue_id"] == "plane-issue-298"
    finally:
        await creator.close()


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

    async def test_duplicate_idempotency_key_returns_fresh_state_not_stale_in_memory(
        self,
        db_session: AsyncSession,
    ) -> None:
        """#242: duplicate approval must re-fetch current DB state, not stale object."""
        service = ApprovalService(
            db=db_session,
            executor=AsyncMock(spec=MacroAgentExecutor),
            permission_service=PermissionService(
                admins={"admin"}, human_approval_verified=True
            ),
        )
        service.executor.start.return_value = {"run_id": "run-test"}
        service.executor.lookup.return_value = None

        task = await _make_task(db_session, TaskState.PLAN_APPROVED)
        contract = _make_contract()
        profile = _make_profile()

        first = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="plane",
            actor="admin",
            idempotency_key="key-dup-242",
        )
        assert first.state == TaskState.RUNNING

        # Re-approve with the same key using the original in-memory task object,
        # which still conceptually represents PLAN_APPROVED. The response must
        # report the true current state from the database.
        second = await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.EXECUTION,
            source="plane",
            actor="admin",
            idempotency_key="key-dup-242",
        )
        assert second.state == TaskState.RUNNING

        approvals = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == task.id)
        )
        assert len(approvals.scalars().all()) == 1

    async def test_idempotency_key_reuse_on_different_task_is_rejected(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        # A key that approved task-a must NOT make task-b report approved.
        task_a = await _make_task(db_session, TaskState.PROPOSED, task_id="task-a")
        task_b = await _make_task(db_session, TaskState.PROPOSED, task_id="task-b")
        contract = _make_contract()
        profile = _make_profile()

        first = await service.approve(
            task=task_a,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="shared-key",
        )
        assert first.state == TaskState.PLAN_APPROVED

        second = await service.approve(
            task=task_b,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="shared-key",
        )

        assert second.state == TaskState.PLAN_APPROVED
        # Each task must have its own approval row even though the key matches.
        approvals_a = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == "task-a")
        )
        approvals_b = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == "task-b")
        )
        assert len(approvals_a.scalars().all()) == 1
        assert len(approvals_b.scalars().all()) == 1

    async def test_idempotency_key_reuse_different_type_is_separate_approval(
        self,
        db_session: AsyncSession,
    ) -> None:
        # Approval type is part of the idempotency scope: reusing the same key
        # for a different type and different task must create a new approval.
        service = ApprovalService(
            db=db_session,
            executor=AsyncMock(spec=MacroAgentExecutor),
            permission_service=PermissionService(admins={"admin", "human-1"}),
        )
        service.executor.start.return_value = {"run_id": "run-test"}
        service.executor.lookup.return_value = None

        task_plan = await _make_task(
            db_session, TaskState.PROPOSED, task_id="task-plan"
        )
        task_plan2 = await _make_task(
            db_session, TaskState.PROPOSED, task_id="task-plan2"
        )
        contract = _make_contract()
        profile = _make_profile()

        result_plan = await service.approve(
            task=task_plan,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="telegram",
            actor="human-1",
            idempotency_key="shared-key",
        )
        assert result_plan.state == TaskState.PLAN_APPROVED

        # Same key reused for PLAN on a different task must also be a fresh
        # approval (not idempotent) because idempotency is scoped to task.
        result_plan2 = await service.approve(
            task=task_plan2,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="dashboard",
            actor="human-1",
            idempotency_key="shared-key",
        )
        assert result_plan2.state == TaskState.PLAN_APPROVED

        approvals_a = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == task_plan.id)
        )
        approvals_b = await service.db.execute(
            Approval.__table__.select().where(Approval.task_id == task_plan2.id)
        )
        assert len(approvals_a.scalars().all()) == 1
        assert len(approvals_b.scalars().all()) == 1


class TestApprovalServicePolicyViolations:
    async def test_policy_violation_raises_value_error(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)

        with pytest.raises(PolicyViolationError, match="Policy violation"):
            await service.approve(
                task=task,
                contract=_make_contract(harness="forbidden-harness"),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="admin",
                idempotency_key="key-policy-audit",
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

        service = ApprovalService(
            db=db_session,
            policy_engine=AlwaysDeny,
            permission_service=PermissionService(admins={"admin"}),
        )
        task = await _make_task(db_session, TaskState.PROPOSED)

        with pytest.raises(PolicyViolationError, match="injected-deny"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="human-1",
                idempotency_key="key-injected",
            )


class TestApprovalServiceRejectedApprovalsPersistAudit:
    async def test_self_approval_rejection_persists_audit_row(
        self,
        db_session: AsyncSession,
    ) -> None:
        service = ApprovalService(
            db=db_session, permission_service=PermissionService(admins={"admin"})
        )
        task = await _make_task(db_session, TaskState.PROPOSED)

        with pytest.raises(PolicyViolationError, match="cannot approve their own task"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="agent-1",
                idempotency_key="key-self-audit",
            )

        entries = await _audit_rows_for_task(db_session, task.id)
        assert any(
            e.event_type == "approval_rejected"
            and e.payload.get("reason") == "self-approval"
            for e in entries
        )

    async def test_permission_denied_persists_audit_row(
        self,
        db_session: AsyncSession,
    ) -> None:
        service = ApprovalService(
            db=db_session, permission_service=PermissionService(admins={"admin"})
        )
        task = await _make_task(db_session, TaskState.PROPOSED)

        with pytest.raises(PolicyViolationError, match="may not request"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="telegram",
                actor="system",
                idempotency_key="key-permission-audit",
            )

        entries = await _audit_rows_for_task(db_session, task.id)
        assert any(
            e.event_type == "approval_rejected"
            and e.payload.get("reason") == "permission_denied"
            for e in entries
        )

    async def test_policy_violation_persists_audit_row(
        self,
        service: ApprovalService,
        db_session: AsyncSession,
    ) -> None:
        task = await _make_task(db_session, TaskState.PLAN_APPROVED)

        with pytest.raises(PolicyViolationError, match="Policy violation"):
            await service.approve(
                task=task,
                contract=_make_contract(harness="forbidden-harness"),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="admin",
                idempotency_key="key-exec-bad",
            )

        entries = await _audit_rows_for_task(db_session, task.id)
        assert any(
            e.event_type == "approval_rejected" and "violations" in e.payload
            for e in entries
        )

    async def test_concurrent_modification_persists_audit_row(
        self,
        isolated_db: tuple,
    ) -> None:
        """Stale task read losing CAS update commits audit before raising."""
        from sqlalchemy import select as sa_select

        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-concurrent",
                project_id="proj-1",
                state=TaskState.PLAN_APPROVED,
                proposed_by="agent-1",
            )
            seed.add(task)
            await seed.commit()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.return_value = {"run_id": "run-first"}
        fake_executor.lookup.return_value = None

        # Open session A and load the stale task object BEFORE session B
        # commits the approval.
        session_a = local_session()
        task_a = await session_a.scalar(
            sa_select(Task).where(Task.id == "task-concurrent")
        )
        assert task_a is not None
        assert task_a.state == TaskState.PLAN_APPROVED
        assert task_a.version == 0

        # Session B commits an EXECUTION approval first. Session A still
        # holds a PLAN_APPROVED copy in memory; when it calls approve(), the
        # in-memory state-machine check passes (PLAN_APPROVED -> EXEC_APPROVED)
        # but the atomic UPDATE's WHERE clause fails because the database row
        # has already advanced, so the concurrent-modification guard fires.
        async with local_session() as session_b:
            service_b = ApprovalService(
                db=session_b,
                executor=fake_executor,
                permission_service=PermissionService(
                    admins={"admin"}, human_approval_verified=True
                ),
            )
            task_b = await session_b.scalar(
                sa_select(Task).where(Task.id == "task-concurrent")
            )
            assert task_b is not None
            result = await service_b.approve(
                task=task_b,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-first",
            )
            assert result.state == TaskState.RUNNING
            await session_b.commit()

        service_a = ApprovalService(
            db=session_a,
            executor=fake_executor,
            permission_service=PermissionService(
                admins={"admin"}, human_approval_verified=True
            ),
        )
        with pytest.raises(ValueError, match="Concurrent modification"):
            await service_a.approve(
                task=task_a,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="test",
                actor="admin",
                idempotency_key="key-second",
            )
        # The rejection audit log was flushed inside approve(); commit it
        # before checking with a separate session.
        await session_a.commit()
        await session_a.close()

        async with local_session() as check:
            entries = await check.execute(
                sa_select(AuditLog).where(AuditLog.task_id == "task-concurrent")
            )
            rows = entries.scalars().all()
            assert any(
                e.event_type == "approval_rejected"
                and e.payload.get("reason") == "concurrent_modification"
                for e in rows
            ), [(e.event_type, repr(e.payload)) for e in rows]


class TestApprovalServiceSelfApprovalPrevention:
    async def test_proposer_cannot_approve_own_task(
        self,
        db_session: AsyncSession,
    ) -> None:
        service = ApprovalService(
            db=db_session, permission_service=PermissionService(admins={"admin"})
        )
        task = await _make_task(db_session, TaskState.PROPOSED)
        # Simulate the proposer attempting to approve their own task.
        with pytest.raises(PolicyViolationError, match="cannot approve their own task"):
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
        service = ApprovalService(
            db=db_session, permission_service=PermissionService(admins={"admin"})
        )
        task = await _make_task(db_session, TaskState.PROPOSED)
        for forbidden_actor in ("system", "agent"):
            with pytest.raises(PolicyViolationError, match="may not request"):
                await service.approve(
                    task=task,
                    contract=_make_contract(),
                    profile=_make_profile(),
                    approval_type=ApprovalType.PLAN,
                    source="plane",
                    actor=forbidden_actor,
                    idempotency_key=f"key-{forbidden_actor}",
                )

    async def test_ghost_proposer_cannot_bypass_self_approval_via_known_proposers(
        self,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """Live-reproduces #376: proposed_by is a free-form, unauthenticated,
        client-supplied string, so the exact-match self-approval guard is
        trivially bypassed by proposing under any string that differs from
        the actor who later approves. Here the same real actor ("admin")
        proposes as "ghost-identity-nobody-owns" and then approves its own
        task through both PLAN and EXECUTION.

        Pre-fix, neither call raises and the task reaches RUNNING. Post-fix,
        with a known-proposer allow-list configured, both are rejected before
        any state change or macro-agent start.
        """
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(
                admins={"admin"}, known_proposers={"admin"}
            ),
        )
        task = Task(
            id="task-ghost",
            project_id="proj-1",
            state=TaskState.PROPOSED,
            proposed_by="ghost-identity-nobody-owns",
        )
        db_session.add(task)
        await db_session.flush()

        with pytest.raises(PolicyViolationError, match="not a recognized actor"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="admin",
                idempotency_key="key-ghost-plan",
            )
        assert task.state == TaskState.PROPOSED

        with pytest.raises(PolicyViolationError, match="not a recognized actor"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="admin",
                idempotency_key="key-ghost-exec",
            )
        assert task.state == TaskState.PROPOSED
        fake_executor.start.assert_not_awaited()

    async def test_ghost_proposer_bypasses_default_unconfigured_allowlist(
        self,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """GitHub-reopened #376 residual, scenario 1: the previous fix made

        `GC_KNOWN_PROPOSERS` an *opt-in* allow-list -- unset (the real
        out-of-the-box default for every existing deployment) meant "allow
        any proposer", so a ghost `proposed_by` sailed through PLAN and
        EXECUTION exactly as before the original fix. Post-fix, an empty/
        unconfigured allow-list must mean DENY, matching `GC_ADMINS`'s
        existing fail-closed default, so this must be rejected with zero
        configuration required.
        """
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(
                admins={"admin"},
                known_proposers=set(),  # unconfigured: the real-world default
                human_approval_verified=True,
            ),
        )
        task = Task(
            id="task-ghost-default",
            project_id="proj-1",
            state=TaskState.PROPOSED,
            proposed_by="ghost-identity-nobody-owns",
        )
        db_session.add(task)
        await db_session.flush()

        with pytest.raises(PolicyViolationError, match="not a recognized actor"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.PLAN,
                source="plane",
                actor="admin",
                idempotency_key="key-ghost-default-plan",
            )
        assert task.state == TaskState.PROPOSED

        with pytest.raises(PolicyViolationError, match="not a recognized actor"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="admin",
                idempotency_key="key-ghost-default-exec",
            )
        assert task.state == TaskState.PROPOSED
        fake_executor.start.assert_not_awaited()

    async def test_cross_known_name_propose_approve_bypass_rejected(
        self,
        db_session: AsyncSession,
        fake_executor: MacroAgentExecutor,
    ) -> None:
        """GitHub-reopened #376 residual, scenario 2: even with

        `known_proposers`/`admins` fully configured, a single caller could
        still propose as one known name ("agent-1") and approve as another
        known name ("admin") -- both names are recognized, so the exact-match
        self-approval guard and the known-proposers check both pass, and
        nothing else proved "admin" was a genuinely distinct, human-backed
        approval channel from whoever created the task. Live-reproduced
        end-to-end (task reached RUNNING) before this fix.

        Post-fix, EXECUTION/MERGE approval additionally requires
        `human_approval_verified` -- proof, independent of the name
        allow-lists, that this specific request came through a real human
        approval channel. Here it is deliberately absent (the default),
        modelling a caller that only knows the recognized names, not a
        distinct human-approval credential -- so the task must never reach
        RUNNING.
        """
        service = ApprovalService(
            db=db_session,
            executor=fake_executor,
            permission_service=PermissionService(
                admins={"admin"},
                known_proposers={"agent-1", "admin"},
                # human_approval_verified defaults to False: no proof of a
                # distinct human approval channel was presented.
            ),
        )
        task = Task(
            id="task-cross-known-name",
            project_id="proj-1",
            state=TaskState.PLAN_APPROVED,
            proposed_by="agent-1",
        )
        db_session.add(task)
        await db_session.flush()

        with pytest.raises(PolicyViolationError, match="may not request"):
            await service.approve(
                task=task,
                contract=_make_contract(),
                profile=_make_profile(),
                approval_type=ApprovalType.EXECUTION,
                source="plane",
                actor="admin",
                idempotency_key="key-cross-known-name-exec",
            )
        assert task.state == TaskState.PLAN_APPROVED
        fake_executor.start.assert_not_awaited()
