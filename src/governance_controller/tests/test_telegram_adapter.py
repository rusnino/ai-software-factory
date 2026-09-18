"""Tests for the Telegram approval adapter."""

import asyncio
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from governance_controller.adapters.telegram import (
    TelegramAdapter,
    TelegramWebhookAuthError,
)


class _StubMacroAgentHandler(http.server.BaseHTTPRequestHandler):
    """Minimal stand-in for the macro-agent service's ``POST /runs``.

    Just enough to let an EXECUTION approval's ``executor.start()`` call
    succeed against a real HTTP server, so a live-server test can drive the
    default EXECUTION path to completion instead of stopping at 403/503.
    """

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"run_id": "stub-run-400", "status": "queued"}).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence default request logging to keep test output clean."""


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


@pytest.fixture
def adapter_with_human_approval_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> TelegramAdapter:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        "controller-secret",
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.human_approval_secret",
        "human-approval-secret",
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

    @pytest.mark.parametrize(
        ("approval_type", "expect_human_approval_secret"),
        [
            ("plan", False),
            ("execution", True),
            ("merge", True),
        ],
    )
    async def test_process_update_scopes_human_approval_secret_to_execution_and_merge(
        self,
        adapter_with_human_approval_secret: TelegramAdapter,
        approval_type: str,
        expect_human_approval_secret: bool,
    ) -> None:
        """#400 follow-up: PLAN never needs -- and must never send --
        ``X-Human-Approval-Secret``. Sending it unconditionally for every
        approval type (the initial #400 fix) leaks the credential over the
        wire on requests that don't require it, widening exposure to any
        HTTP/logging proxy the request happens to transit. Only
        EXECUTION/MERGE -- the types ``api/approvals.py:44-47`` actually
        checks the header for -- may include it.
        """
        update = {
            "message": {
                "text": f"/approve TASK-1 {approval_type}",
                "chat": {"id": 12345},
                "from": {"id": 111, "username": "alice"},
            }
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch(
            "governance_controller.adapters.telegram.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await adapter_with_human_approval_secret.process_update(
                update, secret_token_header="s3cr3t"
            )

        assert result == {"status": "ok"}
        sent_headers = mock_client.post.call_args.kwargs["headers"]
        if expect_human_approval_secret:
            assert sent_headers["X-Human-Approval-Secret"] == "human-approval-secret"
        else:
            assert "X-Human-Approval-Secret" not in sent_headers

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
            # #376: known_proposers now fails closed by default, so the
            # live server needs the test's proposer identity allow-listed.
            "GC_KNOWN_PROPOSERS": "agent-1",
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

    async def test_approve_default_execution_type_sends_human_approval_secret(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#400: default EXECUTION approval must send X-Human-Approval-Secret.

        Sibling of #390's CLI regression test, but for the Telegram adapter.
        Deliberately uses the *default* approval type (no explicit ``plan``
        override) because that is exactly what
        ``test_approve_authenticates_against_real_running_server`` avoided,
        which is how this gap shipped unnoticed: EXECUTION is the type that
        requires ``X-Human-Approval-Secret`` and enforces admin membership,
        so only exercising ``plan`` never touches the missing header at all.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            macro_agent_port = sock.getsockname()[1]

        db_path = tmp_path / "live_telegram_exec.db"
        controller_secret = "live-telegram-controller-secret-400"
        human_approval_secret = "live-telegram-human-approval-secret-400"
        telegram_secret = "live-telegram-webhook-secret-400"
        base_url = f"http://127.0.0.1:{port}"
        macro_agent_base_url = f"http://127.0.0.1:{macro_agent_port}"
        server_env = {
            **os.environ,
            "GC_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "GC_CONTROLLER_API_SECRET": controller_secret,
            "GC_HUMAN_APPROVAL_SECRET": human_approval_secret,
            "GC_PLANE_BASE_URL": "",
            "GC_KNOWN_PROPOSERS": "agent-1",
            # The Telegram actor derived below must be a recognized admin for
            # EXECUTION approval to be permitted at all (PermissionService).
            "GC_ADMINS": "telegram:111:alice",
            # EXECUTION approval synchronously calls the macro-agent's
            # POST /runs; point it at a stub server so the approval can run
            # to completion instead of failing with an unrelated 503 once
            # the header/permission gate under test is passed.
            "GC_MACRO_AGENT_BASE_URL": macro_agent_base_url,
        }

        macro_agent_server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", macro_agent_port), _StubMacroAgentHandler
        )
        macro_agent_thread = threading.Thread(
            target=macro_agent_server.serve_forever, daemon=True
        )
        macro_agent_thread.start()

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

                task_id = "live-telegram-task-400"
                create_response = await client.post(
                    f"{base_url}/tasks",
                    json={
                        "task_contract": {
                            "task_id": task_id,
                            "project_id": "live-telegram-project-400",
                            "proposed_by": "agent-1",
                            "objective": "Live Telegram default EXECUTION approval",
                            "acceptance": ["Telegram EXECUTION approval authenticates"],
                        },
                        "project_profile": {
                            "project_id": "live-telegram-project-400",
                            "project_name": "Live Telegram Project",
                            "repository": {"path": "/tmp/repo"},
                        },
                    },
                    headers={"X-Controller-Secret": controller_secret},
                )
                assert create_response.status_code == 201, create_response.text

                # PLAN approval doesn't require X-Human-Approval-Secret; drive
                # it directly to reach PLAN_APPROVED before the EXECUTION step
                # under test.
                plan_response = await client.post(
                    f"{base_url}/approvals",
                    json={
                        "task_id": task_id,
                        "approval_type": "plan",
                        "source": "test",
                        "actor": "telegram:111:alice",
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                    headers={"X-Controller-Secret": controller_secret},
                )
                assert plan_response.status_code == 200, plan_response.text

                monkeypatch.setattr(
                    "governance_controller.config.settings.controller_api_secret",
                    controller_secret,
                )
                monkeypatch.setattr(
                    "governance_controller.config.settings.human_approval_secret",
                    human_approval_secret,
                )
                adapter = TelegramAdapter(
                    base_url=base_url,
                    secret_token=telegram_secret,
                )
                result = await adapter.process_update(
                    {
                        "message": {
                            "text": f"/approve {task_id}",
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
                # EXECUTION approval synchronously advances READY -> RUNNING
                # once the macro-agent run starts; RUNNING is the real
                # terminal state of a successful default approval, not
                # EXEC_APPROVED.
                assert task_response.json()["state"] == "RUNNING"
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            macro_agent_server.shutdown()
            macro_agent_server.server_close()

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
