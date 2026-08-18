"""Tests for the POST /events endpoint."""

import os
import tempfile

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

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


async def test_post_event_transitions_task(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
) -> None:
    task = Task(
        id="event-task-1",
        project_id="proj-1",
        state=TaskState.RUNNING,
        proposed_by="agent-1",
    )
    client_db_session.add(task)
    await client_db_session.flush()

    response = await async_client.post(
        "/events", json=_make_event("landing:completed", task.id)
    )

    assert response.status_code == 204
    assert task.state == TaskState.AGENT_REVIEW


async def test_post_event_409_on_lost_cas() -> None:
    """A race through the HTTP endpoint returns 409 and does not dead-letter.

    Two separate sessions are used: session A reads the task, session B
    commits a state change that bumps the version, then session A's event
    request loses the CAS and gets HTTP 409. The event_id is not recorded as
    processed, so a retry can eventually succeed.
    """
    from sqlmodel import SQLModel

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_url = f"sqlite+aiosqlite:///{tmp.name}"
    engine = create_async_engine(db_url, echo=False, future=True)
    local_session = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

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
    task_a = await session_a.scalar(
        select(Task).where(Task.id == "event-task-2")
    )
    assert task_a is not None

    # Session B advances the task to AGENT_REVIEW and commits first.
    async with local_session() as session_b:
        task_b = await session_b.scalar(
            select(Task).where(Task.id == "event-task-2")
        )
        assert task_b is not None
        from governance_controller.adapters.macro_agent.event_bridge import (
            EventBridge,
        )

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
                json=_make_event(
                    "landing:completed", task_a.id, event_id="evt-race"
                ),
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
    await engine.dispose()
    os.unlink(tmp.name)


async def test_post_event_rejects_missing_type(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/events",
        json={"metadata": {"controller_task_id": "x"}},
    )

    assert response.status_code == 422


async def test_post_event_invalid_transition_returns_204_and_logs_error(
    async_client: AsyncClient,
    client_db_session: AsyncSession,
) -> None:
    task = Task(
        id="event-task-3",
        project_id="proj-1",
        state=TaskState.PROPOSED,
        proposed_by="agent-1",
    )
    client_db_session.add(task)
    await client_db_session.flush()

    response = await async_client.post(
        "/events", json=_make_event("landing:completed", task.id)
    )

    assert response.status_code == 204
    assert task.state == TaskState.PROPOSED
