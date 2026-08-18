"""Tests for macro-agent client and executor abstraction."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_httpx

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract


@pytest.fixture
def task_contract() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Implement the feature",
        acceptance=["Tests pass", "Code merged"],
        execution=ExecutionConfig(team="default", harness="opencode"),
    )


@pytest.mark.asyncio
async def test_executor_start_sends_correct_payload_and_returns_run_id(
    task_contract: TaskContract,
) -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.start.return_value = {"run_id": "run-abc"}
    executor = MacroAgentExecutor(client=client)

    result = await executor.start(task_contract)

    assert result == {"run_id": "run-abc"}
    client.start.assert_awaited_once_with(
        {
            "task_id": "task-1",
            "team": "default",
            "harness": "opencode",
            "objective": "Implement the feature",
            "acceptance": ["Tests pass", "Code merged"],
        }
    )


@pytest.mark.asyncio
async def test_executor_status_fetches_status_by_run_id() -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.status.return_value = {"run_id": "run-abc", "state": "running"}
    executor = MacroAgentExecutor(client=client)

    result = await executor.status("run-abc")

    assert result["state"] == "running"
    client.status.assert_awaited_once_with("run-abc")


@pytest.mark.asyncio
async def test_executor_cancel_sends_post_cancel() -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.cancel.return_value = {"run_id": "run-abc", "state": "cancelled"}
    executor = MacroAgentExecutor(client=client)

    result = await executor.cancel("run-abc")

    assert result["state"] == "cancelled"
    client.cancel.assert_awaited_once_with("run-abc")


@pytest.mark.asyncio
async def test_executor_collect_fetches_collect() -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.collect.return_value = {"run_id": "run-abc", "result": "done"}
    executor = MacroAgentExecutor(client=client)

    result = await executor.collect("run-abc")

    assert result["result"] == "done"
    client.collect.assert_awaited_once_with("run-abc")


@pytest.mark.asyncio
async def test_http_error_raises_exception(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    client = MacroAgentClient(base_url="https://example.com")
    httpx_mock.add_response(status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await client.start({"task_id": "task-1"})


@pytest.mark.asyncio
async def test_client_uses_configurable_timeout(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"run_id": "run-abc"})

    with patch(
        "governance_controller.adapters.macro_agent.client.settings"
    ) as mock_settings:
        mock_settings.macro_agent_timeout_seconds = 12.5
        client = MacroAgentClient(base_url="https://example.com")
        await client.start({"task_id": "task-1"})

    request = httpx_mock.get_request()
    assert request is not None
