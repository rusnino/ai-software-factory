"""Tests for the local macro-agent service spawner."""

from collections.abc import AsyncGenerator

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
