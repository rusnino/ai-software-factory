"""Tests for the task REST API."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from governance_controller.constants import TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.task import Task
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


_CONTROLLER_SECRET = "controller-secret"


@pytest.fixture(autouse=True)
def _configure_controller_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        _CONTROLLER_SECRET,
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Controller-Secret": _CONTROLLER_SECRET}

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

        response = await async_client.post(
            "/tasks",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

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
        await async_client.post(
            "/tasks",
            json=create_payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        response = await async_client.get(
            "/tasks/api-task-1",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "api-task-1"
        assert body["state"] == TaskState.PROPOSED.value
        assert body["execution_attempts"] == 0

    async def test_execution_attempts_increments_on_retry(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
        client_db_session,
    ) -> None:
        create_payload = {
            "task_contract": sample_contract.model_dump(),
            "project_profile": sample_profile.model_dump(),
        }
        await async_client.post(
            "/tasks",
            json=create_payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        task = await client_db_session.get(Task, sample_contract.task_id)
        task.execution_attempts += 1
        await client_db_session.commit()

        response = await async_client.get(
            "/tasks/api-task-1",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution_attempts"] == 1

    async def test_get_task_nonexistent_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get(
            "/tasks/does-not-exist",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 404
        body = response.json()
        assert body["detail"] == "Task does-not-exist not found"

    async def test_get_task_without_secret_returns_401(
        self,
        async_client: AsyncClient,
    ) -> None:
        """#236: read-side routes require the controller secret."""
        response = await async_client.get("/tasks/api-task-1")
        assert response.status_code == 401

    async def test_create_task_duplicate_returns_409(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        payload = {
            "task_contract": sample_contract.model_dump(),
            "project_profile": sample_profile.model_dump(),
        }

        first = await async_client.post(
            "/tasks",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert first.status_code == 201

        second = await async_client.post(
            "/tasks",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert second.status_code == 409
        body = second.json()
        assert "already exists" in body["detail"]

    async def test_profile_race_not_misreported_as_task_duplicate(
        self,
        async_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        """A project-profile IntegrityError must not say "task already exists"."""
        from governance_controller.services.task_service import TaskService

        async def _failing_create(*_args, **_kwargs) -> None:
            raise IntegrityError(
                "(sqlite3.IntegrityError) "
                "UNIQUE constraint failed: project_profiles.project_id",
                params=None,
                orig=Exception("UNIQUE constraint failed: project_profiles.project_id"),
            )

        monkeypatch.setattr(TaskService, "create", _failing_create)

        payload = {
            "task_contract": sample_contract.model_dump(),
            "project_profile": sample_profile.model_dump(),
        }
        response = await async_client.post(
            "/tasks",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code != 409
        assert "already exists" not in response.text
