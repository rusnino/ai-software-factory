"""Tests for the POST body-size limiting middleware."""

import json as _json

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.middleware import WriteBodySizeLimitMiddleware


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


async def test_post_events_rejects_oversized_body(async_client: AsyncClient) -> None:
    oversized = {"type": "x", "payload": {"x": "y" * (70 * 1024)}}

    response = await async_client.post("/events", json=oversized)

    assert response.status_code == 413


async def test_post_events_rejects_oversized_body_without_content_length(
    async_client: AsyncClient,
) -> None:
    body = _json.dumps({"type": "x", "payload": {"x": "y" * (70 * 1024)}}).encode()

    response = await async_client.post(
        "/events",
        content=body,
        headers={"transfer-encoding": "chunked"},
    )

    assert response.status_code == 413


async def test_post_tasks_rejects_oversized_body(async_client: AsyncClient) -> None:
    oversized = {"task_contract": {"payload": "x" * (70 * 1024)}}

    response = await async_client.post("/tasks", json=oversized)

    assert response.status_code == 413


async def test_post_tasks_rejects_oversized_body_without_content_length(
    async_client: AsyncClient,
) -> None:
    body = _json.dumps({"task_contract": {"payload": "x" * (70 * 1024)}}).encode()

    response = await async_client.post(
        "/tasks",
        content=body,
        headers={"transfer-encoding": "chunked"},
    )

    assert response.status_code == 413


async def test_post_approvals_rejects_oversized_body(
    async_client: AsyncClient,
) -> None:
    oversized = {"approval_type": "EXECUTION", "payload": "x" * (70 * 1024)}

    response = await async_client.post("/approvals", json=oversized)

    assert response.status_code == 413


async def test_post_approvals_rejects_oversized_body_without_content_length(
    async_client: AsyncClient,
) -> None:
    body = _json.dumps(
        {"approval_type": "EXECUTION", "payload": "x" * (70 * 1024)}
    ).encode()

    response = await async_client.post(
        "/approvals",
        content=body,
        headers={"transfer-encoding": "chunked"},
    )

    assert response.status_code == 413


async def test_disconnect_mid_oversized_body_does_not_spin(
    async_client: AsyncClient,
) -> None:
    body = _json.dumps({"type": "x", "payload": "y" * (70 * 1024)}).encode()

    # Build a generator that yields the first chunk and then a disconnect,
    # simulating a client that abandons the stream before finishing the body.
    async def _disconnecting_receive():
        yield {"type": "http.request", "body": body, "more_body": True}
        while True:
            yield {"type": "http.disconnect"}

    gen = _disconnecting_receive()
    calls = []

    async def _receive():
        msg = await gen.asend(None)
        calls.append(msg["type"])
        return msg

    sent: list[dict] = []

    async def _send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/events",
        "headers": [],
    }

    middleware = WriteBodySizeLimitMiddleware(app)
    import asyncio

    await asyncio.wait_for(middleware(scope, _receive, _send), timeout=2.0)

    # We should have bailed early and never sent an HTTP response.
    assert len(sent) == 0
    # The drain loop should have seen the first request chunk then a disconnect
    # and stopped, not spun forever.
    assert calls.count("http.disconnect") >= 1
