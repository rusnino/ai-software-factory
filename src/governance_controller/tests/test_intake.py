"""Tests for the intake adapter endpoints."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
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
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_created"
    assert body["category"] == "new_project"
    assert fake_ingestion.calls[0][1].source == "telegram"


async def test_telegram_spam_is_ignored(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
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
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"


async def test_email_intake_creates_draft(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/email",
        json={
            "message_id": "msg-1",
            "from": {"address": "bob@example.com"},
            "subject": "Feature request",
            "body_text": "Add dark mode",
        },
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
    monkeypatch.setattr("governance_controller.config.settings.intake_secret", "")
    response = await async_client.post(
        "/intake/idea",
        json={
            "source": "api",
            "source_id": "api-1",
            "sender": "charlie",
            "subject": "New dashboard",
            "body": "Build a metrics dashboard",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft_created"
    assert fake_ingestion.calls[0][1].source == "api"


async def test_telegram_invalid_secret_returns_403(
    async_client: AsyncClient,
    fake_ingestion: _FakeIngestionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "telegram_webhook_secret_token", "secret")
    monkeypatch.setattr(config.settings, "intake_secret", "")
    response = await async_client.post(
        "/intake/telegram",
        json={"message": {"text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
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

    assert response.status_code == 403


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
