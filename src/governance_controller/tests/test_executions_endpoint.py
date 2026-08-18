"""Tests for the executions REST API endpoint."""

from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task


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


async def test_get_execution_returns_record(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
) -> None:
    task = Task(id="exec-task-1", project_id="proj-1", proposed_by="agent-1")
    client_db_session.add(task)
    execution = Execution(
        id="exec-1",
        task_id="exec-task-1",
        macro_agent_run_id="run-1",
        state=TaskState.RUNNING,
        started_at=datetime.now(UTC),
    )
    client_db_session.add(execution)
    await client_db_session.flush()

    response = await async_client.get("/executions/exec-1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "exec-1"
    assert body["task_id"] == "exec-task-1"
    assert body["macro_agent_run_id"] == "run-1"
    assert body["state"] == TaskState.RUNNING.value


async def test_get_execution_missing_returns_404(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/executions/missing-exec")

    assert response.status_code == 404
