"""Tests for the GET /health endpoint."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.main import app


@pytest.fixture
async def async_client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    async def _override_get_db() -> AsyncSession:
        # Health endpoint only calls execute on the session; yield a mock.
        mock_session = AsyncMock(spec=AsyncSession)
        yield mock_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_health_returns_ok_when_db_is_connected(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["version"] == "0.1.0"


async def test_health_returns_degraded_when_db_is_disconnected(
    async_client: AsyncClient,
) -> None:
    async def _failing_get_db() -> AsyncSession:
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute.side_effect = RuntimeError("database unreachable")
        yield mock_session

    app.dependency_overrides[get_db] = _failing_get_db
    try:
        response = await async_client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "disconnected"
    assert body["version"] == "0.1.0"
