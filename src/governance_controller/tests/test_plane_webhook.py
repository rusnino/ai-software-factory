"""Tests for the Plane CE webhook receiver."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.models.task import Task
from governance_controller.schemas import ProjectProfile, RepositoryConfig
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract


def _event(
    task_id: str = "TASK-1",
    event_type: str = "state.changed",
    previous_state: str = "Proposed",
    current_state: str = "Plan Approved",
    actor: str = "human@example.com",
    actor_type: str = "human",
    operation: str = "single_update",
) -> dict[str, Any]:
    return {
        "source": "plane",
        "event_type": event_type,
        "task_id": task_id,
        "project_id": "proj-1",
        "payload": {
            "previous": {"state": previous_state},
            "current": {"state": current_state},
            "actor": actor,
            "actor_type": actor_type,
            "operation": operation,
        },
    }


@pytest_asyncio.fixture
async def seeded_db(isolated_db: tuple) -> AsyncGenerator[AsyncSession]:
    """Yield a session backed by a file-based isolated DB with seeded data."""
    from governance_controller.db import get_db
    from governance_controller.main import app

    engine, session_local = isolated_db

    async with session_local() as session:
        profile = ProjectProfile(
            project_id="proj-1",
            project_name="Controller",
            repository=RepositoryConfig(path="https://example.com/repo"),
            execution={"timeout_minutes": 60},
            security={"forbidden_paths": []},
            git={"signed_commits": "optional"},
        )
        session.add(
            ProjectProfileModel(
                project_id=profile.project_id,
                profile_json=profile.model_dump(mode="json"),
            )
        )

        contract = TaskContract(
            task_id="TASK-1",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Implement a thing",
            acceptance=["It works"],
            execution=ExecutionConfig(max_retries=2),
        )
        task = Task(
            id="TASK-1",
            project_id="proj-1",
            state=TaskState.PROPOSED,
            proposed_by="agent-1",
            task_contract_json=contract.model_dump(mode="json"),
        )
        session.add(task)
        await session.commit()

        async def _override_get_db() -> AsyncGenerator[AsyncSession]:
            yield session

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def async_client(seeded_db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Return an HTTP client pointed at the FastAPI app."""
    from governance_controller.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_webhook_ignores_non_state_event(async_client: AsyncClient) -> None:
    event = _event(event_type="task.updated")
    response = await async_client.post("/webhooks/plane", json=event)
    assert response.status_code == 204


async def test_webhook_rejects_bulk_operation(async_client: AsyncClient) -> None:
    event = _event(operation="bulk_update")
    response = await async_client.post("/webhooks/plane", json=event)
    assert response.status_code == 204


async def test_webhook_rejects_non_human_actor(async_client: AsyncClient) -> None:
    event = _event(actor_type="system")
    response = await async_client.post("/webhooks/plane", json=event)
    assert response.status_code == 204


async def test_webhook_rejects_unknown_state_transition(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
) -> None:
    event = _event(previous_state="Proposed", current_state="In Progress")
    response = await async_client.post("/webhooks/plane", json=event)
    # Proposed -> In Progress is not an approval-eligible transition, so ignored.
    assert response.status_code == 204


async def test_webhook_missing_task_returns_404(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/webhooks/plane", json=_event(task_id="MISSING")
    )
    assert response.status_code == 404


async def test_webhook_stale_state_returns_409(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
) -> None:
    # Task is PROPOSED but webhook claims previous Plane state was Plan Approved.
    event = _event(previous_state="Plan Approved", current_state="Approved")
    response = await async_client.post("/webhooks/plane", json=event)
    assert response.status_code == 409


async def test_webhook_plan_approval_advances_state(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
) -> None:
    response = await async_client.post("/webhooks/plane", json=_event())
    assert response.status_code == 204

    refreshed = await seeded_db.scalar(
        select(Task).where(Task.id == "TASK-1")  # type: ignore[arg-type]
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.PLAN_APPROVED


async def test_webhook_self_approval_returns_403(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
) -> None:
    # task.proposed_by is "agent-1"; actor is "agent-1" -> self-approval.
    event = _event(actor="agent-1")
    response = await async_client.post("/webhooks/plane", json=event)
    assert response.status_code == 403
