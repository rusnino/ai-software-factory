"""Tests for macro-agent client and executor abstraction."""

import http.server
import threading
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
        execution=ExecutionConfig(team="default", harness="opencode", role="planner"),
    )


@pytest.mark.asyncio
async def test_executor_start_sends_correct_payload_and_returns_run_id(
    task_contract: TaskContract,
) -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.start.return_value = {"run_id": "run-abc"}
    executor = MacroAgentExecutor(client=client)

    result = await executor.start(task_contract, controller_execution_id="exec-1")

    assert result == {"run_id": "run-abc"}
    client.start.assert_awaited_once_with(
        {
            "task_id": "task-1",
            "team": "default",
            "harness": "opencode",
            "role": "planner",
            "objective": "Implement the feature",
            "acceptance": ["Tests pass", "Code merged"],
            "timeout_minutes": 60,
            "max_retries": 2,
            "sandbox": "worktree",
            "max_parallel_agents": 3,
            "uses_docker_socket": False,
            "destructive_shell": False,
            "spawn_subagents": False,
            "network_access": "restricted",
            "force_push": False,
            "signed_commits": False,
            "metadata": {
                "controller_task_id": "task-1",
                "controller_execution_id": "exec-1",
                "opentasks_id": None,
                "project_id": "project-1",
            },
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
async def test_start_rejects_malformed_response_from_real_http_server() -> None:
    """#285: a start response without run_id must fail at the HTTP boundary."""

    class _MalformedResponseHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"queued"}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _MalformedResponseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = MacroAgentClient(
            base_url=f"http://127.0.0.1:{server.server_port}"
        )
        with pytest.raises(ValueError, match="Invalid macro-agent start response"):
            await client.start({"task_id": "task-malformed-response"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_client_uses_configurable_timeout(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"run_id": "run-abc"})

    with patch(
        "governance_controller.adapters.macro_agent.client.settings"
    ) as mock_settings:
        mock_settings.macro_agent_timeout_seconds = 12.5
        mock_settings.macro_agent_api_secret = ""
        client = MacroAgentClient(base_url="https://example.com")
        await client.start({"task_id": "task-1"})

    request = httpx_mock.get_request()
    assert request is not None


@pytest.mark.asyncio
async def test_client_sends_auth_secret(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"run_id": "run-abc"})

    with patch(
        "governance_controller.adapters.macro_agent.client.settings"
    ) as mock_settings:
        mock_settings.macro_agent_timeout_seconds = 12.5
        mock_settings.macro_agent_api_secret = "secret"
        client = MacroAgentClient(base_url="https://example.com")
        await client.start({"task_id": "task-1"})

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-Macro-Agent-Secret"] == "secret"


@pytest.mark.asyncio
async def test_executor_feedback_sends_payload_to_run_id(
    task_contract: TaskContract,
) -> None:
    client = AsyncMock(spec=MacroAgentClient)
    client.feedback.return_value = {"feedback_count": 1}
    executor = MacroAgentExecutor(client=client)

    feedback = {"verification_report": {"passed": False}, "reason": "failed check"}
    result = await executor.feedback("run-abc", feedback)

    assert result["feedback_count"] == 1
    client.feedback.assert_awaited_once_with("run-abc", feedback)


@pytest.mark.asyncio
async def test_client_feedback_posts_to_feedback_endpoint(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"feedback_count": 1})
    client = MacroAgentClient(base_url="https://example.com")

    result = await client.feedback(
        "run-abc", {"verification_report": {"passed": False}}
    )

    assert result["feedback_count"] == 1
    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.path == "/runs/run-abc/feedback"
