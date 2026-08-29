"""Tests for the global per-IP rate-limiting middleware (#248)."""

import time
from collections import deque

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from governance_controller.config import settings
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.middleware import (
    _requests_by_ip,
    _requests_last_access,
    reset_rate_limits,
)

_CONTROLLER_SECRET = "controller-secret"


@pytest.fixture(autouse=True)
def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_health_endpoint_is_exempt_from_rate_limit(
    async_client: AsyncClient,
) -> None:
    for _ in range(5):
        response = await async_client.get("/health")
        assert response.status_code == 200


async def test_authenticated_requests_are_rate_limited(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    reset_rate_limits()

    for _ in range(3):
        response = await async_client.get(
            "/tasks/does-not-exist",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert response.status_code in (200, 404)

    response = await async_client.get(
        "/tasks/does-not-exist",
        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
    )
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.text


async def test_rate_limit_can_be_disabled(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    reset_rate_limits()

    for _ in range(5):
        response = await async_client.get(
            "/tasks/does-not-exist",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert response.status_code == 404


async def test_rate_limit_tracks_per_ip(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    reset_rate_limits()

    _requests_by_ip["other-ip"] = deque([0.0, 0.0])

    for _ in range(2):
        response = await async_client.get(
            "/tasks/does-not-exist",
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        assert response.status_code == 404

    response = await async_client.get(
        "/tasks/does-not-exist",
        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
    )
    assert response.status_code == 429


async def test_rate_limit_evicts_least_recently_used_ip(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#250: the per-IP tracking dict must not grow without bound."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 100)
    monkeypatch.setattr(settings, "rate_limit_max_ips", 2)
    reset_rate_limits()

    # Two distinct IPs are tracked.
    _requests_by_ip["ip-1"] = deque([time.monotonic()])
    _requests_last_access["ip-1"] = time.monotonic()
    _requests_by_ip["ip-2"] = deque([time.monotonic()])
    _requests_last_access["ip-2"] = time.monotonic()

    # A third IP request evicts the least-recently-accessed one.
    response = await async_client.get(
        "/tasks/does-not-exist",
        headers={"X-Controller-Secret": _CONTROLLER_SECRET, "X-Forwarded-For": "ip-3"},
    )
    assert response.status_code == 404
    assert len(_requests_by_ip) <= 2
    assert "ip-1" not in _requests_by_ip
