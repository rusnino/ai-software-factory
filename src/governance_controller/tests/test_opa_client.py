"""Tests for the OPA client and policy backend selector."""

from pathlib import Path
from typing import Any

import pytest

from governance_controller.adapters.opa_client import OPAClient, OPAClientError
from governance_controller.constants import ApprovalType
from governance_controller.schemas import (
    Check,
    CompletionContract,
    ProjectExecutionConfig,
    ProjectProfile,
    RepositoryConfig,
    ScopeCheck,
    SecurityConfig,
)
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


async def test_opa_client_sends_minimal_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPA receives a data-minimized input, not the full contract dump."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    captured: dict[str, object] | None = None

    class _CapturingOPAClient(OPAClient):
        async def evaluate(self, input_data: dict[str, object]) -> dict[str, Any]:
            nonlocal captured
            captured = input_data
            return {"allow": True, "violations": []}

    backend = PolicyEngineBackend(opa_client=_CapturingOPAClient())

    contract = TaskContract(
        task_id="T-1",
        project_id="P-1",
        proposed_by="agent",
        objective="This sensitive objective should not be sent to OPA",
        acceptance=["Pass"],
        execution=ExecutionConfig(harness="opencode"),
    )
    profile = ProjectProfile(
        project_id="P-1",
        project_name="SecretProject",
        repository=RepositoryConfig(path="/repo"),
        execution={"allowed_harnesses": ["opencode"]},
        security={"forbidden_paths": [".env"]},
    )

    result = await backend.evaluate(
        contract, profile, ApprovalType.PLAN, actor="human-1"
    )

    assert result.allowed is True
    assert captured is not None
    assert captured.get("task_id") == "T-1"
    assert "objective" not in captured
    assert "commands" in captured
    assert captured.get("allowed_harnesses") == ["opencode"]
    assert captured.get("approval", {}).get("actor") == "human-1"
    assert "security" in captured
    assert "git" in captured
    assert "execution" in captured
    assert "role" in captured.get("execution", {})


async def test_opa_input_contains_policy_parity_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPA receives the policy facts needed for role and resource checks."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    captured: dict[str, object] | None = None

    class _CapturingOPAClient(OPAClient):
        async def evaluate(self, input_data: dict[str, object]) -> dict[str, Any]:
            nonlocal captured
            captured = input_data
            return {"allow": True, "violations": []}

    backend = PolicyEngineBackend(opa_client=_CapturingOPAClient())
    contract = TaskContract(
        task_id="T-parity",
        project_id="P-parity",
        proposed_by="agent",
        objective="Check parity facts",
        acceptance=["facts are present"],
        execution=ExecutionConfig(
            harness="opencode",
            role="worker",
            timeout_minutes=30,
            max_retries=1,
        ),
        verification={"commands": ["uv run pytest -q"]},
    )
    profile = ProjectProfile(
        project_id="P-parity",
        project_name="ParityProject",
        repository=RepositoryConfig(path="/repo"),
        execution={
            "allowed_harnesses": ["opencode"],
            "timeout_minutes": 60,
            "max_retries": 2,
        },
    )

    result = await backend.evaluate(contract, profile, ApprovalType.PLAN)

    assert result.allowed is True
    assert captured is not None
    assert captured["has_objective"] is True
    assert captured["has_acceptance"] is True
    execution = captured["execution"]
    assert isinstance(execution, dict)
    assert execution["timeout_minutes"] == 30
    assert execution["max_retries"] == 1
    profile_execution = captured["profile_execution"]
    assert profile_execution == {"timeout_minutes": 60, "max_retries": 2}
    harness_roles = captured["harness_roles"]
    assert isinstance(harness_roles, dict)
    assert "worker" in harness_roles["opencode"]
    assert captured["parsed_commands"] == [
        {"raw": "uv run pytest -q", "argv": ["uv", "run", "pytest", "-q"], "error": ""}
    ]
    assert captured["forbidden_path_conflicts"] == []


def test_opa_input_extracts_no_space_sed_output_path() -> None:
    forbidden = "/tmp/blocked"
    command = "sed -n '1w/tmp/blocked/marker' input"
    contract = TaskContract(
        task_id="T-sed-path",
        project_id="P-sed-path",
        proposed_by="agent",
        objective="Check sed output path",
        acceptance=["path is checked"],
        execution=ExecutionConfig(harness="opencode"),
        forbidden_paths=[forbidden],
        completion_contract=CompletionContract(
            task_id="T-sed-path",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="sed output path"),
        ),
    )
    profile = ProjectProfile(
        project_id="P-sed-path",
        project_name="SedPathProject",
        repository=RepositoryConfig(path="/repo"),
        execution=ProjectExecutionConfig(allowed_harnesses=["opencode"]),
        security=SecurityConfig(forbidden_paths=[forbidden]),
    )

    input_data = PolicyEngineBackend._minimal_opa_input(
        contract, profile, ApprovalType.EXECUTION
    )

    assert input_data["forbidden_path_conflicts"] == ["/tmp/blocked/marker"]


def test_opa_input_extracts_attached_tar_archive_path() -> None:
    forbidden = "/tmp/blocked"
    command = "tar -cf/tmp/blocked/archive.tar input"
    contract = TaskContract(
        task_id="T-tar-path",
        project_id="P-tar-path",
        proposed_by="agent",
        objective="Check tar archive path",
        acceptance=["path is checked"],
        execution=ExecutionConfig(harness="opencode"),
        forbidden_paths=[forbidden],
        completion_contract=CompletionContract(
            task_id="T-tar-path",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="tar archive path"),
        ),
    )
    profile = ProjectProfile(
        project_id="P-tar-path",
        project_name="TarPathProject",
        repository=RepositoryConfig(path="/repo"),
        execution=ProjectExecutionConfig(allowed_harnesses=["opencode"]),
        security=SecurityConfig(forbidden_paths=[forbidden]),
    )

    input_data = PolicyEngineBackend._minimal_opa_input(
        contract, profile, ApprovalType.EXECUTION
    )

    assert input_data["forbidden_path_conflicts"] == ["/tmp/blocked/archive.tar"]


@pytest.mark.parametrize(
    ("command", "expected_path"),
    [
        ("tar --dir=/tmp/blocked -tf archive.tar", "/tmp/blocked"),
        ("tar --dire=/tmp/blocked -tf archive.tar", "/tmp/blocked"),
        (
            "tar --files-from=/tmp/blocked/list -cf archive.tar input",
            "/tmp/blocked/list",
        ),
        ("tar -T /tmp/blocked/list -cf archive.tar input", "/tmp/blocked/list"),
        ("tar cf/tmp/blocked/archive.tar input", "/tmp/blocked/archive.tar"),
        (
            "cp --targe=/tmp/blocked/.git source",
            "/tmp/blocked/.git",
        ),
        (
            "git config --fil=/tmp/blocked/.git/config filter.pwn.clean touch",
            "/tmp/blocked/.git/config",
        ),
        ("go build -o=/tmp/blocked/tool ./cmd", "/tmp/blocked/tool"),
        ("go build -o /tmp/blocked/tool ./cmd", "/tmp/blocked/tool"),
        ("pytest --basetemp=/tmp/blocked/tmp", "/tmp/blocked/tmp"),
        ("pytest --junitxml=/tmp/blocked/result.xml", "/tmp/blocked/result.xml"),
        ("pytest --log-file=/tmp/blocked/test.log", "/tmp/blocked/test.log"),
        ("pytest --debug=/tmp/blocked/debug.log", "/tmp/blocked/debug.log"),
        ("make -f/tmp/blocked/Makefile", "/tmp/blocked/Makefile"),
    ],
)
def test_opa_input_extracts_abbreviated_tar_paths(
    command: str, expected_path: str
) -> None:
    forbidden = "/tmp/blocked"
    contract = TaskContract(
        task_id="T-tar-path",
        project_id="P-tar-path",
        proposed_by="agent",
        objective="Check tar path",
        acceptance=["path is checked"],
        execution=ExecutionConfig(harness="opencode"),
        forbidden_paths=[forbidden],
        completion_contract=CompletionContract(
            task_id="T-tar-path",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="tar path"),
        ),
    )
    profile = ProjectProfile(
        project_id="P-tar-path",
        project_name="TarPathProject",
        repository=RepositoryConfig(path="/repo"),
        execution=ProjectExecutionConfig(allowed_harnesses=["opencode"]),
        security=SecurityConfig(forbidden_paths=[forbidden]),
    )

    input_data = PolicyEngineBackend._minimal_opa_input(
        contract, profile, ApprovalType.EXECUTION
    )

    assert input_data["forbidden_path_conflicts"] == [expected_path]


def test_opa_input_extracts_bare_relative_positional_path() -> None:
    forbidden = "secret.txt"
    command = "cat secret.txt"
    contract = TaskContract(
        task_id="T-bare-path",
        project_id="P-bare-path",
        proposed_by="agent",
        objective="Check bare path",
        acceptance=["path is checked"],
        execution=ExecutionConfig(harness="opencode"),
        forbidden_paths=[forbidden],
        completion_contract=CompletionContract(
            task_id="T-bare-path",
            required=[Check(type="command", command=command)],
            scope_check=ScopeCheck(description="bare path"),
        ),
    )
    profile = ProjectProfile(
        project_id="P-bare-path",
        project_name="BarePathProject",
        repository=RepositoryConfig(path="/repo"),
        execution=ProjectExecutionConfig(allowed_harnesses=["opencode"]),
        security=SecurityConfig(forbidden_paths=[forbidden]),
    )

    input_data = PolicyEngineBackend._minimal_opa_input(
        contract, profile, ApprovalType.EXECUTION
    )

    assert input_data["forbidden_path_conflicts"] == [forbidden]


async def test_opa_client_raises_on_http_error(httpx_mock) -> None:
    client = OPAClient(base_url="http://opa.example.com")
    httpx_mock.add_response(status_code=500, text="boom")

    with pytest.raises(OPAClientError, match="OPA returned 500"):
        await client.evaluate({})


async def test_opa_client_sends_auth_token_when_configured(
    httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPA client sends Authorization bearer token when configured."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")
    monkeypatch.setattr(config.settings, "opa_api_token", "secret-token")

    httpx_mock.add_response(json={"result": {"allow": True, "violations": []}})
    client = OPAClient()
    await client.evaluate({})

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer secret-token"


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


async def test_backend_denies_malformed_opa_violations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed OPA decisions must fail closed instead of silently allowing."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "opa_base_url", "http://opa.example.com")

    fake = _FakeOPAClient({"allow": True, "violations": "not-a-list"})
    backend = PolicyEngineBackend(opa_client=fake)
    contract = TaskContract(
        task_id="T-malformed-opa",
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
    assert any("malformed" in violation.lower() for violation in result.violations)


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


async def test_backend_fails_closed_when_opa_unreachable(
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


def test_governance_rego_uses_data_minimized_input_shape() -> None:
    """#216: rego must read the data-minimized input, not input.contract/profile."""
    rego_path = (
        Path(__file__).resolve().parents[1] / "policies" / "opa" / "governance.rego"
    )
    assert rego_path.exists(), f"governance.rego not found at {rego_path}"
    text = rego_path.read_text()

    assert "input.contract" not in text, "rego still references full contract input"
    assert "input.profile" not in text, "rego still references full profile input"
    for key in ["execution", "security", "git", "commands", "allowed_harnesses"]:
        needle = f'object.get(input, "{key}"'
        assert needle in text, f"rego missing data-minimized {key} input"


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
