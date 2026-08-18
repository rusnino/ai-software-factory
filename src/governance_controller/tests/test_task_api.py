"""Tests for the task REST API."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from governance_controller.constants import TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.schemas import ProjectProfile, RepositoryConfig, TaskContract


@pytest.fixture
def sample_contract() -> TaskContract:
    return TaskContract(
        task_id="api-task-1",
        project_id="api-proj-1",
        proposed_by="agent-1",
        objective="Build the API",
        acceptance=["API endpoints exist", "Tests pass"],
    )


@pytest.fixture
def sample_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="api-proj-1",
        project_name="API Project",
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


class TestTaskApi:
    async def test_create_task_returns_201(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        payload = {
            "task_contract": sample_contract.model_dump(),
            "project_profile": sample_profile.model_dump(),
        }

        response = await async_client.post("/tasks", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "api-task-1"
        assert body["state"] == TaskState.PROPOSED.value
        assert body["project_id"] == "api-proj-1"
        assert body["created_at"] is not None

    async def test_get_task_returns_200(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        create_payload = {
            "task_contract": sample_contract.model_dump(),
            "project_profile": sample_profile.model_dump(),
        }
        await async_client.post("/tasks", json=create_payload)

        response = await async_client.get("/tasks/api-task-1")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "api-task-1"
        assert body["state"] == TaskState.PROPOSED.value
