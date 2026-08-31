"""Tests for the Telegram approval adapter."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from governance_controller.adapters.telegram import (
    TelegramAdapter,
    TelegramWebhookAuthError,
)


@pytest.fixture
def adapter() -> TelegramAdapter:
    return TelegramAdapter(base_url="http://localhost:8000")


@pytest.fixture
def adapter_with_secret(monkeypatch: pytest.MonkeyPatch) -> TelegramAdapter:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        "controller-secret",
    )
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
            headers={"X-Controller-Secret": "controller-secret"},
        )
        assert result == {"status": "ok"}

    async def test_approve_authenticates_against_real_running_server(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#284: Telegram approval must authenticate its internal API call."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        db_path = tmp_path / "live_telegram.db"
        controller_secret = "live-telegram-controller-secret"
        telegram_secret = "live-telegram-webhook-secret"
        base_url = f"http://127.0.0.1:{port}"
        server_env = {
            **os.environ,
            "GC_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "GC_CONTROLLER_API_SECRET": controller_secret,
            "GC_PLANE_BASE_URL": "",
        }
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "governance_controller.main:app",
                "--port",
                str(port),
            ],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 15
            async with httpx.AsyncClient(timeout=0.5) as client:
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        output = server.stdout.read() if server.stdout else ""
                        pytest.fail(f"Controller server exited early:\n{output}")
                    try:
                        response = await client.get(f"{base_url}/health")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.2)
                else:
                    pytest.fail("Controller server did not become ready in time")

                task_id = "live-telegram-task-284"
                create_response = await client.post(
                    f"{base_url}/tasks",
                    json={
                        "task_contract": {
                            "task_id": task_id,
                            "project_id": "live-telegram-project-284",
                            "proposed_by": "agent-1",
                            "objective": "Live Telegram authentication regression",
                            "acceptance": ["Telegram approval authenticates"],
                        },
                        "project_profile": {
                            "project_id": "live-telegram-project-284",
                            "project_name": "Live Telegram Project",
                            "repository": {"path": "/tmp/repo"},
                        },
                    },
                    headers={"X-Controller-Secret": controller_secret},
                )
                assert create_response.status_code == 201, create_response.text

                monkeypatch.setattr(
                    "governance_controller.config.settings.controller_api_secret",
                    controller_secret,
                )
                adapter = TelegramAdapter(
                    base_url=base_url,
                    secret_token=telegram_secret,
                )
                result = await adapter.process_update(
                    {
                        "message": {
                            "text": f"/approve {task_id} plan",
                            "chat": {"id": 12345},
                            "from": {"id": 111, "username": "alice"},
                        }
                    },
                    secret_token_header=telegram_secret,
                )

                assert result == {"status": "ok"}
                task_response = await client.get(
                    f"{base_url}/tasks/{task_id}",
                    headers={"X-Controller-Secret": controller_secret},
                )
                assert task_response.status_code == 200
                assert task_response.json()["state"] == "PLAN_APPROVED"
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

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
