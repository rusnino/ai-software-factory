"""Tests for the audit log REST API endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.task import Task
from governance_controller.services.audit_service import AuditService

_CONTROLLER_SECRET = "controller-secret"


@pytest.fixture(autouse=True)
def _configure_controller_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        _CONTROLLER_SECRET,
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


async def test_get_audit_log_returns_entries(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
) -> None:
    task = Task(id="audit-task-1", project_id="proj-1", proposed_by="agent-1")
    client_db_session.add(task)
    await client_db_session.flush()

    await AuditService.log(
        db=client_db_session,
        event_type="state_change",
        task_id="audit-task-1",
        actor="human-1",
        source="test",
        payload={"previous_state": "PROPOSED", "new_state": "PLAN_APPROVED"},
    )

    response = await async_client.get(
        "/tasks/audit-task-1/audit-log",
        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "state_change"
    assert body[0]["actor"] == "human-1"


async def test_get_audit_log_for_missing_task_returns_404(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get(
        "/tasks/missing-task/audit-log",
        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
    )

    assert response.status_code == 404


async def test_get_audit_log_without_secret_returns_401(
    async_client: AsyncClient,
) -> None:
    """#236: read-side audit route requires the controller secret."""
    response = await async_client.get("/tasks/audit-task-1/audit-log")
    assert response.status_code == 401
