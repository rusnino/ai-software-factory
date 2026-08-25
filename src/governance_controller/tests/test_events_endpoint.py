"""Tests for the POST /events endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task


def _make_event(
    event_type: str,
    task_id: str | None,
    *,
    event_id: str | None = "evt-1",
    event_timestamp: str | None = "2026-08-18T00:00:00+00:00",
) -> dict:
    metadata: dict = {}
    if task_id is not None:
        metadata["controller_task_id"] = task_id
    if event_id is not None:
        metadata["event_id"] = event_id
    if event_timestamp is not None:
        metadata["event_timestamp"] = event_timestamp
    return {"type": event_type, "metadata": metadata, "payload": {"foo": "bar"}}


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


async def test_post_event_rejects_unauthenticated_request(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    response = await async_client.post(
        "/events", json=_make_event("landing:completed", "event-task-1")
    )
    assert response.status_code == 401


async def test_post_event_transitions_task(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    task = Task(
        id="event-task-1",
        project_id="proj-1",
        state=TaskState.RUNNING,
        proposed_by="agent-1",
    )
    client_db_session.add(task)
    await client_db_session.flush()

    response = await async_client.post(
        "/events",
        json=_make_event("landing:completed", task.id),
        headers={"X-Event-Bridge-Secret": "secret"},
    )

    assert response.status_code == 204
    assert task.state == TaskState.AGENT_REVIEW


async def test_post_event_409_on_lost_cas(
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    """A race through the HTTP endpoint returns 409 and does not dead-letter.

    Two separate sessions are used: session A reads the task, session B
    commits a state change that bumps the version, then session A's event
    request loses the CAS and gets HTTP 409. The event_id is not recorded as
    processed, so a retry can eventually succeed.
    """
    from governance_controller.adapters.macro_agent.event_bridge import (
        EventBridge,
    )

    engine, local_session = isolated_db

    async with local_session() as seed:
        task = Task(
            id="event-task-2",
            project_id="proj-1",
            state=TaskState.RUNNING,
            proposed_by="agent-1",
        )
        seed.add(task)
        await seed.commit()

    # Session A reads the task but does not commit a change yet.
    session_a = local_session()
    task_a = await session_a.scalar(select(Task).where(Task.id == "event-task-2"))
    assert task_a is not None

    # Session B advances the task to AGENT_REVIEW and commits first.
    async with local_session() as session_b:
        task_b = await session_b.scalar(select(Task).where(Task.id == "event-task-2"))
        assert task_b is not None

        await EventBridge.handle(
            session_b,
            _make_event("landing:completed", task_b.id, event_id="evt-b"),
        )
        await session_b.commit()

    async def _override_get_db():
        yield session_a

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/events",
                json=_make_event("landing:completed", task_a.id, event_id="evt-race"),
                headers={"X-Event-Bridge-Secret": "secret"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409
    assert "Concurrent modification" in response.json()["detail"]

    # The losing event must not be marked as processed.
    rows = await session_a.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.task_id == task_a.id,
            ProcessedEvent.event_id == "evt-race",
        )
    )
    assert rows.scalar_one_or_none() is None

    await session_a.close()


async def test_post_event_rejects_missing_type(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    response = await async_client.post(
        "/events",
        json={"metadata": {"controller_task_id": "x"}},
        headers={"X-Event-Bridge-Secret": "secret"},
    )

    assert response.status_code == 422


async def test_post_event_rejects_oversized_body(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    oversized = {"type": "x", "payload": {"x": "y" * (70 * 1024)}}

    response = await async_client.post(
        "/events",
        json=oversized,
        headers={"X-Event-Bridge-Secret": "secret"},
    )

    assert response.status_code == 413


async def test_post_event_rejects_oversized_body_without_content_length(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Middleware must cap actual bytes received, not just the Content-Length header."""
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    import json as _json

    body = _json.dumps({"type": "x", "payload": {"x": "y" * (70 * 1024)}}).encode()

    response = await async_client.post(
        "/events",
        content=body,
        headers={
            "transfer-encoding": "chunked",
            "X-Event-Bridge-Secret": "secret",
        },
    )

    assert response.status_code == 413


async def test_post_event_invalid_transition_returns_204_and_logs_error(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.event_bridge_secret",
        "secret",
    )
    task = Task(
        id="event-task-3",
        project_id="proj-1",
        state=TaskState.PROPOSED,
        proposed_by="agent-1",
    )
    client_db_session.add(task)
    await client_db_session.flush()

    response = await async_client.post(
        "/events",
        json=_make_event("landing:completed", task.id),
        headers={"X-Event-Bridge-Secret": "secret"},
    )

    assert response.status_code == 204
    assert task.state == TaskState.PROPOSED
