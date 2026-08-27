"""Phase 1 end-to-end smoke test for the full Governance Controller flow."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from governance_controller.adapters.macro_agent.event_bridge import EventBridge
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.api.approvals import get_approval_service
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas import (
    ProjectProfile,
    RepositoryConfig,
    TaskContract,
)
from governance_controller.services.approval_service import ApprovalService


@pytest.fixture
def smoke_contract() -> TaskContract:
    return TaskContract(
        task_id="phase1-task-1",
        project_id="phase1-proj-1",
        proposed_by="agent-1",
        objective="End-to-end Phase 1 smoke test",
        acceptance=["State machine reaches DONE"],
    )


_CONTROLLER_SECRET = "controller-secret"


@pytest.fixture(autouse=True)
def _configure_controller_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        _CONTROLLER_SECRET,
    )


@pytest.fixture
def smoke_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="phase1-proj-1",
        project_name="Phase 1 Smoke Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )


@pytest.fixture
def mock_executor() -> AsyncMock:
    return AsyncMock(spec=MacroAgentExecutor)


@pytest_asyncio.fixture
async def async_client(client_db_session, mock_executor) -> AsyncClient:
    async def _override_get_db():
        yield client_db_session

    def _override_get_approval_service(db=Depends(get_db)) -> ApprovalService:
        return ApprovalService(db=db, executor=mock_executor)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_approval_service] = _override_get_approval_service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_approval_service, None)


def _approval_payload(task_id: str, approval_type: ApprovalType) -> dict:
    return {
        "task_id": task_id,
        "approval_type": approval_type.value,
        "source": "smoke-test",
        "actor": "admin",
        "timestamp": "2026-08-18T12:00:00+00:00",
    }


@pytest.mark.asyncio
class TestPhase1Smoke:
    async def test_full_phase1_path_to_done(
        self,
        async_client: AsyncClient,
        client_db_session,
        mock_executor: AsyncMock,
        smoke_contract: TaskContract,
        smoke_profile: ProjectProfile,
    ) -> None:
        mock_executor.start.return_value = {"run_id": "smoke-run-1"}

        # 1. Create a governed task.
        create_response = await async_client.post(
            "/tasks",
            json={
                "task_contract": smoke_contract.model_dump(),
                "project_profile": smoke_profile.model_dump(),
            },
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert create_response.status_code == 201
        body = create_response.json()
        task_id = body["id"]
        assert body["state"] == TaskState.PROPOSED.value

        # 2. PLAN approval.
        plan_response = await async_client.post(
            "/approvals",
            json=_approval_payload(task_id, ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert plan_response.status_code == 200
        assert plan_response.json()["state"] == TaskState.PLAN_APPROVED.value

        # 3. EXECUTION approval triggers macro-agent start.
        exec_response = await async_client.post(
            "/approvals",
            json=_approval_payload(task_id, ApprovalType.EXECUTION),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert exec_response.status_code == 200
        assert exec_response.json()["state"] == TaskState.RUNNING.value
        mock_executor.start.assert_awaited_once()
        call_args, _ = mock_executor.start.await_args
        assert len(call_args) == 2
        assert call_args[1] is not None

        # 4. Execution record exists.
        execution = await client_db_session.scalar(
            select(Execution).where(Execution.task_id == task_id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "smoke-run-1"
        assert execution.state == TaskState.RUNNING

        def make_event(event_type: str) -> dict:
            return {
                "type": event_type,
                "metadata": {"controller_task_id": task_id},
                "payload": {"run_id": "smoke-run-1"},
            }

        # 5. Simulate macro-agent events through the event bridge.
        # Conflict arises while running, is resolved, then landing:completed
        # triggers verification and advances the task to HUMAN_REVIEW.
        await EventBridge.handle(client_db_session, make_event("conflict:created"))
        await self._assert_task_state(client_db_session, task_id, TaskState.BLOCKED)

        await EventBridge.handle(client_db_session, make_event("conflict:resolved"))
        await self._assert_task_state(client_db_session, task_id, TaskState.RUNNING)

        await EventBridge.handle(client_db_session, make_event("landing:completed"))
        await self._assert_task_state(
            client_db_session, task_id, TaskState.HUMAN_REVIEW
        )

        # Duplicate landing:completed from HUMAN_REVIEW is ignored.
        await EventBridge.handle(client_db_session, make_event("landing:completed"))
        await self._assert_task_state(
            client_db_session, task_id, TaskState.HUMAN_REVIEW
        )

        # 6. MERGE approval.
        merge_response = await async_client.post(
            "/approvals",
            json=_approval_payload(task_id, ApprovalType.MERGE),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert merge_response.status_code == 200
        assert merge_response.json()["state"] == TaskState.DONE.value

        # 7. Final task state is DONE.
        task = await client_db_session.scalar(select(Task).where(Task.id == task_id))
        assert task.state == TaskState.DONE

        # Audit log assertions for key transitions.
        audit_result = await client_db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task_id).order_by(AuditLog.id)
        )
        audit_entries = audit_result.scalars().all()
        assert len(audit_entries) > 0

        approval_entries = [e for e in audit_entries if e.event_type == "approval"]
        merge_approvals = [
            e
            for e in approval_entries
            if e.payload.get("approval_type") == ApprovalType.MERGE.value
        ]
        assert len(merge_approvals) == 1
        assert merge_approvals[0].payload["new_state"] == TaskState.DONE.value

        state_changes = [e for e in audit_entries if e.event_type == "state_change"]
        assert any(
            e.payload.get("new_state") == TaskState.RUNNING.value for e in state_changes
        )
        # #241: the READY state_change audit entry must reflect the actual
        # pre-READY state (EXEC_APPROVED), not the stale PLAN_APPROVED value.
        ready_changes = [
            e
            for e in state_changes
            if e.payload.get("new_state") == TaskState.READY.value
        ]
        assert len(ready_changes) == 1
        assert (
            ready_changes[0].payload.get("previous_state")
            == TaskState.EXEC_APPROVED.value
        )

        exec_start_entries = [
            e for e in audit_entries if e.event_type == "execution_start"
        ]
        assert len(exec_start_entries) == 1
        assert exec_start_entries[0].payload["macro_agent_run_id"] == "smoke-run-1"

        assert any(
            e.event_type == "macro_agent_landing:completed" for e in audit_entries
        )
        assert any(
            e.event_type == "macro_agent_conflict:created" for e in audit_entries
        )
        assert any(
            e.event_type == "macro_agent_conflict:resolved" for e in audit_entries
        )

    async def _assert_task_state(
        self, db_session, task_id: str, expected: TaskState
    ) -> None:
        task = await db_session.scalar(select(Task).where(Task.id == task_id))
        assert task.state == expected
