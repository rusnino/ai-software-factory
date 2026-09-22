"""Tests for the local macro-agent service spawner."""

import socket
import subprocess
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.local_service import (
    LocalMacroAgentService,
)
from governance_controller.adapters.macro_agent.spawner import macro_agent_backend


@pytest_asyncio.fixture
async def local_service() -> AsyncGenerator[LocalMacroAgentService]:
    """Start a local macro-agent service on a free port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    async with LocalMacroAgentService(host="127.0.0.1", port=port) as service:
        yield service


async def test_local_service_starts_and_responds(
    local_service: LocalMacroAgentService,
) -> None:
    client = MacroAgentClient(base_url=local_service.base_url)
    result = await client.start({"task_id": "test-1", "objective": "do work"})

    assert "run_id" in result
    assert result["status"] == "queued"


async def test_local_service_fails_when_port_squatted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#203: a stale process on the port must not be accepted as our service."""
    import http.server
    import threading

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    # Start a minimal HTTP server on the chosen port to simulate a stale service.
    handler = http.server.BaseHTTPRequestHandler
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        service = LocalMacroAgentService(host="127.0.0.1", port=port)
        with pytest.raises(RuntimeError, match="port"):
            async with service:
                pass

        # The new subprocess should have been terminated/not be running.
        assert service._proc is None or service._proc.poll() is not None
    finally:
        server.shutdown()
        server.server_close()


async def test_local_service_detects_subprocess_crash_before_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#203: a subprocess that dies before binding must be reported as failure."""

    class _CrashedProc:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.pid = 12345
            self.stdout = None

        def poll(self) -> int:
            return 3

        def wait(self, timeout: float | None = None) -> int:
            return 3

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    monkeypatch.setattr(subprocess, "Popen", _CrashedProc)
    service = LocalMacroAgentService(host="127.0.0.1", port=port)

    with pytest.raises(RuntimeError, match="did not bind"):
        async with service:
            pass


async def test_spawner_yields_client_for_local_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    monkeypatch.setattr(
        "governance_controller.config.settings.macro_agent_start_local", True
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.macro_agent_local_port", port
    )

    async with macro_agent_backend() as client:
        result = await client.start({"task_id": "test-2", "objective": "do work"})

    assert "run_id" in result
    assert result["status"] == "queued"


async def test_local_service_propagates_controller_secret_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local service must receive the same secret its client sends."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "macro_agent_api_secret", "shared-secret")
    service = LocalMacroAgentService(host="127.0.0.1", port=39999)
    monkeypatch.setattr(service, "_service_root", lambda: "/tmp")
    monkeypatch.setattr(service, "_wait_for_port", lambda *_args: True)
    monkeypatch.setattr(service, "_health_check", AsyncMock(return_value=True))

    process = MagicMock()
    process.pid = 999999
    process.poll.return_value = None
    process.wait.return_value = 0
    with patch(
        "governance_controller.adapters.macro_agent.local_service.subprocess.Popen",
        return_value=process,
    ) as popen:
        async with service:
            pass

    environment = popen.call_args.kwargs["env"]
    assert environment["MACRO_AGENT_SERVICE_API_SECRET"] == "shared-secret"


async def test_local_service_does_not_leak_controller_secrets_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#403: the Controller's own secrets must not reach the child's real env.

    This is a live reproduction, not a mock: it sets marker secrets in the
    Controller's own environment, starts the real subprocess tree (the ``uv``
    wrapper and its ``python`` child), and reads each process's actual
    environment from ``/proc/<pid>/environ`` -- the same vector the reporter
    used to demonstrate the leak.
    """
    import psutil

    monkeypatch.setenv("GC_HUMAN_APPROVAL_SECRET", "marker-approval-secret")
    monkeypatch.setenv("GC_CONTROLLER_API_SECRET", "marker-controller-secret")
    monkeypatch.setenv(
        "GC_DATABASE_URL",
        "postgresql+asyncpg://marker-user:marker-pass@localhost/marker-db",
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def read_environ(pid: int) -> str:
        with open(f"/proc/{pid}/environ", "rb") as f:
            return f.read().decode("utf-8", errors="replace")

    async with LocalMacroAgentService(host="127.0.0.1", port=port) as service:
        assert service._proc is not None
        root_process = psutil.Process(service._proc.pid)
        processes = [root_process, *root_process.children(recursive=True)]

        for process in processes:
            environ_text = read_environ(process.pid)
            assert "marker-approval-secret" not in environ_text
            assert "marker-controller-secret" not in environ_text
            assert "marker-pass" not in environ_text
            assert "GC_HUMAN_APPROVAL_SECRET" not in environ_text
            assert "GC_CONTROLLER_API_SECRET" not in environ_text
            assert "GC_DATABASE_URL" not in environ_text
