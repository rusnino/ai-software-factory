"""Tests for the OPA client and policy backend selector."""

from typing import Any

import pytest

from governance_controller.adapters.opa_client import OPAClient, OPAClientError
from governance_controller.constants import ApprovalType
from governance_controller.schemas import ProjectProfile, RepositoryConfig
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.policy_engine import PolicyResult
from governance_controller.services.policy_engine_backend import PolicyEngineBackend


class _FakeOPAClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def evaluate(self, input_data: dict[str, object]) -> dict[str, Any]:
        self.calls.append(input_data)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_opa_client_parses_allow_and_violations(httpx_mock) -> None:
    client = OPAClient(base_url="http://opa.example.com")
    httpx_mock.add_response(
        url="http://opa.example.com/v1/data/governance/approve",
        json={
            "result": {
                "allow": False,
                "violations": ["Harness not allowed"],
            }
        },
    )

    result = await client.evaluate({"foo": "bar"})

    assert result["allow"] is False
    assert result["violations"] == ["Harness not allowed"]


async def test_opa_client_raises_on_http_error(httpx_mock) -> None:
    client = OPAClient(base_url="http://opa.example.com")
    httpx_mock.add_response(status_code=500, text="boom")

    with pytest.raises(OPAClientError, match="OPA returned 500"):
        await client.evaluate({})


async def test_opa_client_raises_when_unconfigured() -> None:
    client = OPAClient(base_url="")

    with pytest.raises(OPAClientError, match="OPA base URL is not configured"):
        await client.evaluate({})


async def test_backend_uses_opa_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    fake = _FakeOPAClient({"allow": True, "violations": []})
    backend = PolicyEngineBackend(opa_client=fake)

    contract = TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="Do work",
        acceptance=["Pass"],
        execution=ExecutionConfig(harness="opencode"),
    )
    profile = ProjectProfile(
        project_id="P-1",
        project_name="Test",
        repository=RepositoryConfig(path="/repo"),
        execution={"allowed_harnesses": ["opencode"]},
    )

    result = await backend.evaluate(contract, profile, ApprovalType.PLAN)

    assert result.allowed is True
    assert result.violations == []
    assert fake.calls[0]["approval_type"] == "plan"


async def test_backend_runs_embedded_before_opa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedded hardening must run even when OPA is configured."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    fake = _FakeOPAClient({"allow": True, "violations": []})
    backend = PolicyEngineBackend(opa_client=fake)

    contract = TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="Do work",
        acceptance=["Pass"],
        execution=ExecutionConfig(harness="opencode"),
        # Embedding a forbidden shell metacharacter should be rejected by the
        # embedded engine regardless of OPA.
        completion_contract={
            "task_id": "T-1",
            "required": [
                {
                    "type": "malicious",
                    "command": "sed '1e touch /tmp/x' file.txt",
                    "expect_exit": 0,
                }
            ],
            "forbidden_path_check": {"paths": []},
            "scope_check": {
                "description": "No scope constraints",
                "allowed_paths": [],
                "forbidden_paths": [],
            },
        },
    )
    profile = ProjectProfile(
        project_id="P-1",
        project_name="Test",
        repository=RepositoryConfig(path="/repo"),
        execution={"allowed_harnesses": ["opencode"]},
    )

    result = await backend.evaluate(contract, profile, ApprovalType.PLAN)

    assert result.allowed is False
    assert any("sed" in v.lower() for v in result.violations)


async def test_backend_falls_back_to_embedded_when_opa_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    fake = _FakeOPAClient(OPAClientError("timeout"))
    backend = PolicyEngineBackend(opa_client=fake)

    contract = TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="Do work",
        acceptance=["Pass"],
        execution=ExecutionConfig(harness="opencode"),
    )
    profile = ProjectProfile(
        project_id="P-1",
        project_name="Test",
        repository=RepositoryConfig(path="/repo"),
        execution={"allowed_harnesses": ["opencode"]},
    )

    result = await backend.evaluate(contract, profile, ApprovalType.PLAN)

    assert result.allowed is False
    assert any("OPA" in v for v in result.violations)


async def test_backend_uses_embedded_engine_without_opa_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "")

    contract = TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="Do work",
        acceptance=["Pass"],
        execution=ExecutionConfig(harness="opencode"),
    )
    profile = ProjectProfile(
        project_id="P-1",
        project_name="Test",
        repository=RepositoryConfig(path="/repo"),
        execution={"allowed_harnesses": ["opencode"]},
    )

    backend = PolicyEngineBackend()
    result = await backend.evaluate(contract, profile, ApprovalType.PLAN)

    assert isinstance(result, PolicyResult)
