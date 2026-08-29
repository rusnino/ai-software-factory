"""Tests for the Telegram approval adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from governance_controller.adapters.telegram import (
    TelegramAdapter,
    TelegramWebhookAuthError,
)


@pytest.fixture
def adapter() -> TelegramAdapter:
    return TelegramAdapter(base_url="http://localhost:8000")


@pytest.fixture
def adapter_with_secret() -> TelegramAdapter:
    return TelegramAdapter(base_url="http://localhost:8000", secret_token="s3cr3t")


class TestTelegramAdapter:
    async def test_process_update_approve_command_sends_request(
        self,
        adapter_with_secret: TelegramAdapter,
    ) -> None:
        update = {
            "message": {
                "text": "/approve TASK-1 execution",
                "chat": {"id": 12345},
                "from": {"id": 111, "username": "alice"},
            }
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "task_id": "TASK-1",
            "state": "EXEC_APPROVED",
            "approved": True,
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch(
            "governance_controller.adapters.telegram.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter_with_secret.process_update(
                update, secret_token_header="s3cr3t"
            )

        mock_client.post.assert_awaited_once_with(
            "http://localhost:8000/approvals",
            json={
                "task_id": "TASK-1",
                "approval_type": "execution",
                "source": "telegram",
                "actor": "telegram:111:alice",
                "timestamp": mock_client.post.call_args.kwargs["json"]["timestamp"],
                "comment": None,
            },
        )
        assert result == {"status": "ok"}

    async def test_process_update_unrelated_message_returns_ignored(
        self,
        adapter_with_secret: TelegramAdapter,
    ) -> None:
        update = {"message": {"text": "hello world"}}

        result = await adapter_with_secret.process_update(
            update, secret_token_header="s3cr3t"
        )

        assert result == {"status": "ignored"}

    async def test_process_update_rejects_missing_secret_token(
        self, adapter_with_secret: TelegramAdapter
    ) -> None:
        update = {
            "message": {
                "text": "/approve TASK-1 execution",
                "from": {"id": 111, "username": "alice"},
            }
        }

        with pytest.raises(TelegramWebhookAuthError):
            await adapter_with_secret.process_update(update)

    async def test_process_update_rejects_wrong_secret_token(
        self, adapter_with_secret: TelegramAdapter
    ) -> None:
        update = {
            "message": {
                "text": "/approve TASK-1 execution",
                "from": {"id": 111, "username": "alice"},
            }
        }

        with pytest.raises(TelegramWebhookAuthError):
            await adapter_with_secret.process_update(
                update, secret_token_header="wrong"
            )

    async def test_process_update_accepts_valid_secret_token(
        self, adapter_with_secret: TelegramAdapter
    ) -> None:
        update = {
            "message": {
                "text": "/approve TASK-1 execution",
                "from": {"id": 111, "username": "alice"},
            }
        }
        mock_client = AsyncMock()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        with patch(
            "governance_controller.adapters.telegram.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter_with_secret.process_update(
                update, secret_token_header="s3cr3t"
            )

        assert result == {"status": "ok"}

    async def test_derive_actor_prefers_stable_numeric_id(
        self, adapter: TelegramAdapter
    ) -> None:
        """#257: Telegram actor must use the immutable numeric id."""
        message = {"from": {"id": 123, "username": "bob"}}

        assert adapter.derive_actor(message) == "telegram:123:bob"

    async def test_derive_actor_falls_back_to_user_id(
        self, adapter: TelegramAdapter
    ) -> None:
        message = {"from": {"id": 456}}

        assert adapter.derive_actor(message) == "telegram:456"

    async def test_derive_actor_legacy_fallback_when_no_sender(
        self, adapter: TelegramAdapter
    ) -> None:
        message = {}

        assert adapter.derive_actor(message) == "telegram-user"
