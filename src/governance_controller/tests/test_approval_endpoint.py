"""Tests for the approvals REST API endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.schemas import ProjectProfile, RepositoryConfig, TaskContract


@pytest.fixture
def sample_contract() -> TaskContract:
    return TaskContract(
        task_id="approval-task-1",
        project_id="approval-proj-1",
        objective="Test approvals via API",
        acceptance=["Approvals advance state"],
    )


@pytest.fixture
def sample_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="approval-proj-1",
        project_name="Approval Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )


@pytest_asyncio.fixture
async def async_client(client_db_session) -> AsyncClient:
    async def _override_get_db():
        yield client_db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _create_task(
    client: AsyncClient,
    contract: TaskContract,
    profile: ProjectProfile,
) -> None:
    payload = {
        "task_contract": contract.model_dump(),
        "project_profile": profile.model_dump(),
    }
    response = await client.post("/tasks", json=payload)
    assert response.status_code == 201


def _approval_payload(
    task_id: str, approval_type: ApprovalType, actor: str = "human-1"
) -> dict:
    return {
        "task_id": task_id,
        "approval_type": approval_type.value,
        "source": "test",
        "actor": actor,
        "timestamp": "2026-08-18T12:00:00+00:00",
    }


class TestApprovalEndpoint:
    async def test_approval_without_previous_plan_returns_409(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        payload = _approval_payload(
            "approval-task-1", ApprovalType.EXECUTION
        )

        response = await async_client.post("/approvals", json=payload)

        assert response.status_code == 409
        assert "PROPOSED -> EXEC_APPROVED" in response.text

    async def test_approval_plan_advances_state(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        payload = _approval_payload("approval-task-1", ApprovalType.PLAN)

        response = await async_client.post("/approvals", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["task_id"] == "approval-task-1"
        assert body["state"] == TaskState.PLAN_APPROVED.value
        assert body["approved"] is True

    async def test_approval_execution_advances_state(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals", json=_approval_payload("approval-task-1", ApprovalType.PLAN)
        )

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.EXECUTION),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == TaskState.EXEC_APPROVED.value
        assert body["approved"] is True

    async def test_approval_merge_advances_to_done(
        self,
        async_client: AsyncClient,
        client_db_session,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals", json=_approval_payload("approval-task-1", ApprovalType.PLAN)
        )
        # Drive the task to HUMAN_REVIEW via direct state changes so merge can fire.
        from governance_controller.models.task import Task
        from governance_controller.services.state_machine import StateMachine

        result = await client_db_session.execute(
            select(Task).where(Task.id == "approval-task-1")
        )
        task = result.scalar_one()
        StateMachine.transition(task, TaskState.EXEC_APPROVED)
        StateMachine.transition(task, TaskState.READY)
        StateMachine.transition(task, TaskState.RUNNING)
        StateMachine.transition(task, TaskState.AGENT_REVIEW)
        StateMachine.transition(task, TaskState.HUMAN_REVIEW)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.MERGE),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == TaskState.DONE.value
        assert body["approved"] is True
