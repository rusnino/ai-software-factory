"""Tests for the global per-IP rate-limiting middleware (#248)."""

import asyncio
import os
import time
from collections import deque
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from governance_controller.config import settings
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.middleware import (
    InMemoryRateLimitMiddleware,
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


async def test_duplicate_intake_does_not_consume_global_ip_budget(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#300: duplicate intake retries must not starve a new submission."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "intake_rate_limit_per_minute", 10)
    monkeypatch.setattr(settings, "intake_secret", "intake-secret")
    monkeypatch.setattr(settings, "plane_base_url", "")
    reset_rate_limits()

    def payload(source_id: str) -> dict[str, str]:
        return {
            "source": "api",
            "source_id": source_id,
            "sender": "alice@example.com",
            "subject": "Feature request",
            "body": "Build a useful feature",
        }

    headers = {"X-Intake-Secret": "intake-secret"}
    first = await async_client.post(
        "/intake/idea", json=payload("idea-1"), headers=headers
    )
    duplicate_one = await async_client.post(
        "/intake/idea", json=payload("idea-1"), headers=headers
    )
    duplicate_two = await async_client.post(
        "/intake/idea", json=payload("idea-1"), headers=headers
    )
    new_submission = await async_client.post(
        "/intake/idea", json=payload("idea-2"), headers=headers
    )

    assert first.status_code == 200
    assert duplicate_one.status_code == 409
    assert duplicate_two.status_code == 409
    assert new_submission.status_code == 200


async def test_unauthenticated_intake_requests_consume_global_ip_budget(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid intake attempts must not bypass the global IP limiter."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "intake_secret", "configured-secret")
    reset_rate_limits()

    def payload(source_id: str) -> dict[str, str]:
        return {
            "source": "api",
            "source_id": source_id,
            "sender": "unauthenticated@example.com",
            "subject": "Feature request",
            "body": "Build a useful feature",
        }

    responses = [
        await async_client.post("/intake/idea", json=payload("unauth-1")),
        await async_client.post("/intake/idea", json=payload("unauth-2")),
        await async_client.post("/intake/idea", json=payload("unauth-3")),
    ]

    assert [response.status_code for response in responses] == [401, 401, 429]


async def test_inflight_duplicate_intake_burst_does_not_starve_new_submission(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#300: duplicate retries already in the handler must not fill IP slots."""
    from governance_controller.api.intake import get_idea_ingestion_service
    from governance_controller.main import app
    from governance_controller.schemas.intake import ClassifiedIdea, RawIdea
    from governance_controller.services.idea_ingestion_service import (
        DuplicateIntakeError,
    )

    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "intake_secret", "intake-secret")
    reset_rate_limits()

    duplicate_started = asyncio.Event()
    release_duplicates = asyncio.Event()
    started_count = 0

    class _SlowDuplicateService:
        def classify(self, idea: RawIdea) -> ClassifiedIdea:
            return ClassifiedIdea(
                idea=idea,
                category="new_project",
                confidence=1.0,
                reason="test",
            )

        async def create_draft(
            self,
            classified: ClassifiedIdea,
            project_id: str | None = None,
            db: Any = None,
        ) -> dict[str, object]:
            nonlocal started_count
            if classified.idea.source_id == "duplicate":
                started_count += 1
                if started_count == 3:
                    duplicate_started.set()
                await release_duplicates.wait()
                raise DuplicateIntakeError("duplicate")
            return {"id": "new-draft"}

    service = _SlowDuplicateService()
    app.dependency_overrides[get_idea_ingestion_service] = lambda: service

    def payload(source_id: str) -> dict[str, str]:
        return {
            "source": "api",
            "source_id": source_id,
            "sender": "alice@example.com",
            "subject": "Feature request",
            "body": "Build a useful feature",
        }

    headers = {"X-Intake-Secret": "intake-secret"}
    duplicate_requests = [
        asyncio.create_task(
            async_client.post(
                "/intake/idea", json=payload("duplicate"), headers=headers
            )
        )
        for _ in range(3)
    ]
    try:
        await duplicate_started.wait()
        new_submission = await async_client.post(
            "/intake/idea", json=payload("new"), headers=headers
        )
        release_duplicates.set()
        duplicate_responses = await asyncio.gather(*duplicate_requests)
    finally:
        release_duplicates.set()
        app.dependency_overrides.pop(get_idea_ingestion_service, None)

    assert [response.status_code for response in duplicate_responses] == [409] * 3
    assert new_submission.status_code == 200


async def test_stale_intake_release_cannot_delete_replacement_ip_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An evicted request must not release a newer bucket for the same IP."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 10)
    monkeypatch.setattr(settings, "rate_limit_max_ips", 1)
    reset_rate_limits()

    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def downstream(scope: dict[str, Any], _receive: Any, send: Any) -> None:
        if scope["state"].get("name") == "first":
            first_started.set()
            await release_first.wait()
            scope["state"]["release_intake_rate_limit"]()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = InMemoryRateLimitMiddleware(downstream)

    async def invoke(name: str, path: str, ip: str) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "client": (ip, 1234),
            "state": {"name": name},
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: dict[str, object]) -> None:
            return None

        await middleware(scope, receive, send)

    first = asyncio.create_task(invoke("first", "/intake/idea", "ip-1"))
    await first_started.wait()
    await invoke("evictor", "/tasks", "ip-2")
    await invoke("replacement", "/tasks", "ip-1")
    release_first.set()
    await first

    assert "ip-1" in _requests_by_ip
    assert len(_requests_by_ip["ip-1"]) == 1


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_inflight_duplicate_intake_burst_uses_real_postgres_sessions(
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#300: concurrent real intake requests cannot starve a new submission."""
    from governance_controller.api.intake import get_idea_ingestion_service
    from governance_controller.schemas.intake import ClassifiedIdea
    from governance_controller.services.idea_ingestion_service import (
        IdeaIngestionService,
    )

    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    monkeypatch.setattr(settings, "intake_rate_limit_per_minute", 100)
    monkeypatch.setattr(settings, "intake_secret", "intake-secret")
    monkeypatch.setattr(settings, "plane_base_url", "")
    reset_rate_limits()

    duplicate_started = asyncio.Event()
    release_duplicates = asyncio.Event()
    started_count = 0

    class _DelayedIngestionService(IdeaIngestionService):
        async def create_draft(
            self,
            classified: ClassifiedIdea,
            project_id: str | None = None,
            db: Any = None,
        ) -> dict[str, object] | None:
            nonlocal started_count
            if classified.idea.source_id == "duplicate":
                started_count += 1
                if started_count == 3:
                    duplicate_started.set()
                await release_duplicates.wait()
            return await super().create_draft(
                classified, project_id=project_id, db=db
            )

    from governance_controller.main import app

    service = _DelayedIngestionService()
    app.dependency_overrides[get_idea_ingestion_service] = lambda: service

    def payload(source_id: str) -> dict[str, str]:
        return {
            "source": "api",
            "source_id": source_id,
            "sender": "alice@example.com",
            "subject": "Feature request",
            "body": "Build a useful feature",
        }

    headers = {"X-Intake-Secret": "intake-secret"}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            duplicate_requests = [
                asyncio.create_task(
                    client.post(
                        "/intake/idea", json=payload("duplicate"), headers=headers
                    )
                )
                for _ in range(3)
            ]
            await asyncio.wait_for(duplicate_started.wait(), timeout=5)
            new_submission = await client.post(
                "/intake/idea", json=payload("new"), headers=headers
            )
            release_duplicates.set()
            duplicate_responses = await asyncio.gather(*duplicate_requests)
    finally:
        release_duplicates.set()
        app.dependency_overrides.pop(get_idea_ingestion_service, None)

    assert sorted(response.status_code for response in duplicate_responses) == [
        200,
        409,
        409,
    ]
    assert new_submission.status_code == 200
