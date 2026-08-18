"""Tests for the Telegram approval adapter stub."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from governance_controller.adapters.telegram import TelegramAdapter


@pytest.fixture
def adapter() -> TelegramAdapter:
    return TelegramAdapter(base_url="http://localhost:8000")


class TestTelegramAdapter:
    async def test_process_update_approve_command_sends_request(
        self, adapter: TelegramAdapter
    ) -> None:
        update = {
            "message": {
                "text": "/approve TASK-1 execution",
                "chat": {"id": 12345},
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
            result = await adapter.process_update(update)

        mock_client.post.assert_awaited_once_with(
            "http://localhost:8000/approvals",
            json={
                "task_id": "TASK-1",
                "approval_type": "execution",
                "source": "telegram",
                "actor": "telegram-user",
                "timestamp": mock_client.post.call_args.kwargs["json"]["timestamp"],
                "comment": None,
            },
        )
        assert result == {"status": "ok"}

    async def test_process_update_unrelated_message_returns_ignored(
        self, adapter: TelegramAdapter
    ) -> None:
        update = {"message": {"text": "hello world"}}

        result = await adapter.process_update(update)

        assert result == {"status": "ignored"}
