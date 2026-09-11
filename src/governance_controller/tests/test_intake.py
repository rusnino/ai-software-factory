"""Tests for the intake adapter endpoints."""

import asyncio
import hashlib
import hmac
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from governance_controller.api.intake import telegram_intake
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.schemas.intake import RawIdea


class _FakeIngestionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RawIdea]] = []
        self.category = "new_project"
        self.draft: dict[str, object] | None = None

    def classify(self, idea: RawIdea) -> Any:
        self.calls.append(("classify", idea))
        from governance_controller.schemas.intake import ClassifiedIdea

        return ClassifiedIdea(
            idea=idea,
            category=self.category,
            confidence=0.5,
            reason="test",
        )

    async def create_draft(
        self,
        classified: Any,
        project_id: str | None = None,
        db: Any = None,
    ) -> dict[str, object] | None:
        self.calls.append(("create_draft", classified.idea))
        return self.draft or {"id": "draft-1"}


@pytest_asyncio.fixture
async def async_client(client_db_session) -> AsyncGenerator[AsyncClient]:
    """Return an HTTP client pointed at the FastAPI app."""

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


@pytest.fixture
def fake_ingestion(monkeypatch: pytest.MonkeyPatch) -> _FakeIngestionService:
    from governance_controller.api import intake as intake_module

    service = _FakeIngestionService()
    original = intake_module._ServiceContainer.service
    intake_module._ServiceContainer.service = service
    yield service
    intake_module._ServiceContainer.service = original


async def test_telegram_text_creates_draft(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "secret",
    )
    response = await async_client.post(
        "/intake/telegram",
        json={
            "message": {
                "message_id": 1,
                "from": {"username": "alice", "id": 42},
                "text": "We need a new billing module",
            }
        },
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "secret",
            "X-Intake-Secret": "secret",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_created"
    assert body["category"] == "new_project"
    assert fake_ingestion.calls[0][1].source == "telegram"


async def test_telegram_rate_release_does_not_block_event_loop(
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#339: Telegram's synchronous rate callback runs off the event loop."""
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "telegram-secret",
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/intake/telegram",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )

    def _blocking_charge() -> None:
        time.sleep(0.2)

    request.state.charge_intake_ip_rate_limit = _blocking_charge
    request.state.release_intake_rate_limit = lambda: None

    operation = asyncio.create_task(
        telegram_intake(
            {
                "message": {
                    "message_id": 3,
                    "from": {"username": "alice", "id": 42},
                    "text": "A non-spam idea",
                }
            },
            request,
            x_telegram_bot_api_secret_token="telegram-secret",
            ingestion=fake_ingestion,  # type: ignore[arg-type]
            db=None,  # type: ignore[arg-type]
            _authenticated=None,
        )
    )
    started = time.monotonic()
    await asyncio.sleep(0.05)
    elapsed = time.monotonic() - started
    await operation

    assert elapsed < 0.15


async def test_telegram_spam_is_ignored(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "secret",
    )
    fake_ingestion.category = "spam"
    response = await async_client.post(
        "/intake/telegram",
        json={
            "message": {
                "message_id": 2,
                "from": {"username": "spammer", "id": 99},
                "text": "Buy now!!!",
            }
        },
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "secret",
            "X-Intake-Secret": "secret",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"


async def test_email_intake_creates_draft(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/email",
        json={
            "message_id": "msg-1",
            "from": {"address": "bob@example.com"},
            "subject": "Feature request",
            "body_text": "Add dark mode",
        },
        headers={"X-Intake-Secret": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_created"
    assert fake_ingestion.calls[0][1].source == "email"


async def test_generic_idea_intake(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/idea",
        json={
            "source": "api",
            "source_id": "api-1",
            "sender": "charlie",
            "subject": "New dashboard",
            "body": "Build a metrics dashboard",
        },
        headers={"X-Intake-Secret": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_created"
    assert fake_ingestion.calls[0][1].source == "api"


async def test_duplicate_intake_returns_409(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#214: duplicate submissions must return 409, not 500."""
    from governance_controller.services.idea_ingestion_service import (
        DuplicateIntakeError,
    )

    monkeypatch.setattr(
        "governance_controller.config.settings.intake_secret", "secret"
    )
    fake_ingestion.create_draft = AsyncMock(  # type: ignore[method-assign]
        side_effect=DuplicateIntakeError("Duplicate intake submission: idea/1")
    )
    response = await async_client.post(
        "/intake/idea",
        json={
            "source": "idea",
            "source_id": "1",
            "sender": "alice@example.com",
            "subject": "Feature request",
            "body": "We need a thing",
        },
        headers={"X-Intake-Secret": "secret"},
    )

    assert response.status_code == 409
    assert "Duplicate" in response.json()["detail"]


async def test_rate_limited_intake_returns_429(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#214: rate-limit violations must return 429, not 500."""
    from governance_controller.services.idea_ingestion_service import (
        IntakeRateLimitError,
    )

    monkeypatch.setattr(
        "governance_controller.config.settings.intake_secret", "secret"
    )
    fake_ingestion.create_draft = AsyncMock(  # type: ignore[method-assign]
        side_effect=IntakeRateLimitError("Rate limit exceeded")
    )
    response = await async_client.post(
        "/intake/idea",
        json={
            "source": "idea",
            "source_id": "2",
            "sender": "alice@example.com",
            "subject": "Feature request",
            "body": "We need a thing",
        },
        headers={"X-Intake-Secret": "secret"},
    )

    assert response.status_code == 429
    assert "Rate limit" in response.json()["detail"]


async def test_telegram_invalid_secret_returns_403(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "telegram_webhook_secret_token", "secret")
    monkeypatch.setattr(config.settings, "intake_secret", "secret")
    response = await async_client.post(
        "/intake/telegram",
        json={"message": {"text": "hello"}},
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "wrong",
            "X-Intake-Secret": "secret",
        },
    )

    assert response.status_code == 403


async def test_telegram_unconfigured_secret_rejects_by_default(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "",
    )
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/telegram",
        json={"message": {"text": "hello"}},
    )

    assert response.status_code == 401


async def test_intake_email_rejects_missing_secret(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/email",
        json={"message_id": "msg-1", "subject": "X", "body_text": "Y"},
    )
    assert response.status_code == 401


async def test_intake_email_rejects_unconfigured_secret(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/email",
        json={"message_id": "msg-1", "subject": "X", "body_text": "Y"},
    )
    assert response.status_code == 401


async def test_intake_email_accepts_valid_secret(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/email",
        json={
            "message_id": "msg-1",
            "from": {"address": "a@b.com"},
            "subject": "Feature",
            "body_text": "Add dark mode",
        },
        headers={"X-Intake-Secret": "secret"},
    )
    assert response.status_code == 200


async def test_intake_email_accepts_hmac_signature(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    body = (
        b'{"message_id":"msg-2","from":{"address":"a@b.com"},'
        b'"subject":"Feature","body_text":"Add dark mode"}'
    )
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    response = await async_client.post(
        "/intake/email",
        content=body,
        headers={
            "X-Intake-Signature": signature,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 200


async def test_intake_email_rejects_bad_hmac_signature(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    body = b'{"message_id":"msg-2","subject":"X","body_text":"Y"}'
    response = await async_client.post(
        "/intake/email",
        content=body,
        headers={
            "X-Intake-Signature": "bad",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_intake_email_rejects_oversized_body(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/email",
        json={"message_id": "msg-1", "subject": "X", "body_text": "x" * 200_000},
    )
    assert response.status_code == 413


async def test_intake_email_rejects_empty_body(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#356: empty email fields must return 422, not 500."""
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/email",
        json={
            "message_id": "msg-empty",
            "from": {"address": "bob@example.com"},
            "subject": "Feature request",
            "body_text": "",
        },
        headers={"X-Intake-Secret": "secret"},
    )
    assert response.status_code == 422


async def test_intake_email_rejects_oversized_body_under_global_cap(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#356: a single field over RawIdea's max_length returns 422, not 500."""
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    response = await async_client.post(
        "/intake/email",
        json={
            "message_id": "msg-oversized",
            "from": {"address": "bob@example.com"},
            "subject": "Feature request",
            "body_text": "A" * 20_000,
        },
        headers={"X-Intake-Secret": "secret"},
    )
    assert response.status_code == 422


async def test_intake_telegram_media_without_caption(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#356: ordinary media messages without text/caption must not 500."""
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "secret")
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "secret",
    )
    response = await async_client.post(
        "/intake/telegram",
        json={
            "message": {
                "message_id": 5,
                "from": {"username": "alice", "id": 42},
                "photo": [{"file_id": "abc"}],
            }
        },
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "secret",
            "X-Intake-Secret": "secret",
        },
    )
    assert response.status_code == 200


async def test_intake_telegram_rejects_oversized_body(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.telegram_webhook_secret_token",
        "secret",
    )
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/telegram",
        json={"message": {"text": "x" * 200_000}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    assert response.status_code == 413
