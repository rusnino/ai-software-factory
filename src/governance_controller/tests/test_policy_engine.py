"""Tests for the embedded PolicyEngine."""

import pytest

from governance_controller.constants import ApprovalType
from governance_controller.harness import registry
from governance_controller.harness.base import HarnessProvider
from governance_controller.schemas import Check, CompletionContract, ScopeCheck
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.policy_engine import PolicyEngine, PolicyResult


def _make_contract(
    *,
    objective: str = "Implement feature X",
    acceptance: list[str] | None = None,
    harness: str = "opencode",
    forbidden_paths: list[str] | None = None,
    inputs: list[str] | None = None,
    deliverables: list[str] | None = None,
    completion_contract: CompletionContract | None = None,
    uses_docker_socket: bool = False,
    destructive_shell: bool = False,
    verification: dict | None = None,
) -> TaskContract:
    data: dict = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "proposed_by": "agent-1",
        "objective": objective,
        "execution": ExecutionConfig(
            harness=harness,
            uses_docker_socket=uses_docker_socket,
            destructive_shell=destructive_shell,
        ),
        "forbidden_paths": forbidden_paths or [],
    }
    if acceptance is not None:
        data["acceptance"] = acceptance
    else:
        data["acceptance"] = ["feature X passes tests"]
    if inputs is not None:
        data["inputs"] = inputs
    if deliverables is not None:
        data["deliverables"] = deliverables
    if completion_contract is not None:
        data["completion_contract"] = completion_contract
    if verification is not None:
        data["verification"] = verification
    return TaskContract(**data)


def _make_profile(
    *,
    allowed_harnesses: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    merge_requires_human: bool = True,
    docker_socket: str = "deny",
    destructive_shell: str = "deny",
) -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": allowed_harnesses or ["opencode"]},
        security={
            "forbidden_paths": forbidden_paths or [],
            "docker_socket": docker_socket,
            "destructive_shell": destructive_shell,
        },
        git={"merge_requires_human": merge_requires_human},
    )


class TestPolicyEngineValidCases:
    def test_valid_execution_approval_passes(self) -> None:
        contract = _make_contract()
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert isinstance(result, PolicyResult)
        assert result.allowed is True
        assert result.violations == []


class TestPolicyEngineRejections:
    def test_forbidden_harness_rejected(self) -> None:
        contract = _make_contract(harness="forbidden-harness")
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "forbidden-harness" in v and "allowed harness" in v
            for v in result.violations
        )

    def test_security_posture_violations_rejected(self) -> None:
        contract = _make_contract()
        contract.execution.uses_docker_socket = True
        contract.execution.destructive_shell = True
        contract.execution.spawn_subagents = True
        contract.execution.network_access = "unrestricted"
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("Docker socket" in v for v in result.violations)
        assert any("Destructive shell" in v for v in result.violations)
        assert any("subagents" in v for v in result.violations)
        assert any("Unrestricted network" in v for v in result.violations)

    def test_git_settings_enforced_for_merge(self) -> None:
        contract = _make_contract()
        contract.execution.force_push = True
        contract.execution.signed_commits = False
        profile = _make_profile()
        profile.git.force_push = "deny"
        profile.git.signed_commits = "required"

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.MERGE)

        assert result.allowed is False
        assert any("Force push" in v for v in result.violations)
        assert any("Signed commits" in v for v in result.violations)

    @pytest.mark.parametrize(
        ("objective", "acceptance", "expected_message"),
        [
            ("", ["passes tests"], "objective is empty"),
            ("Do thing", [], "acceptance criteria are empty"),
            ("   ", ["passes tests"], "objective is empty"),
        ],
    )
    def test_missing_acceptance_or_objective_rejected(
        self,
        objective: str,
        acceptance: list[str],
        expected_message: str,
    ) -> None:
        contract = _make_contract(objective=objective, acceptance=acceptance)
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.PLAN)

        assert result.allowed is False
        assert any(expected_message in v for v in result.violations)

    def test_forbidden_path_agreement_is_allowed(self) -> None:
        # The task and profile both forbid a path: this is not a conflict.
        contract = _make_contract(forbidden_paths=["/etc/shadow", "/secrets"])
        profile = _make_profile(forbidden_paths=["/etc/shadow", "/root"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True
        assert result.violations == []

    def test_touched_forbidden_path_is_rejected(self) -> None:
        # The task declares an input that the profile forbids.
        contract = _make_contract(inputs=["/etc/shadow", "README.md"])
        profile = _make_profile(forbidden_paths=["/etc/shadow", "/root"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "Task touches forbidden path: /etc/shadow" in v
            for v in result.violations
        )

    def test_nested_forbidden_path_is_rejected(self) -> None:
        # A file inside a forbidden directory must be rejected.
        contract = _make_contract(inputs=["~/.ssh/id_rsa"])
        profile = _make_profile(forbidden_paths=["~/.ssh", "/srv/production"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "Task touches forbidden path: ~/.ssh/id_rsa" in v
            for v in result.violations
        )

    def test_sibling_of_forbidden_path_is_allowed(self) -> None:
        # A path that shares a prefix but is not under the forbidden directory.
        contract = _make_contract(inputs=["~/.ssh_backup"])
        profile = _make_profile(forbidden_paths=["~/.ssh"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True


class TestPolicyEngineCompletionContractShellAllowlist:
    def test_safe_completion_contract_command_passes(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="test", command="uv run pytest")],
                scope_check=ScopeCheck(description="safe check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True

    def test_docker_socket_command_rejected_when_profile_denies(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[
                    Check(
                        type="docker",
                        command="docker -H unix:///var/run/docker.sock ps",
                    )
                ],
                scope_check=ScopeCheck(description="docker check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("docker socket" in v for v in result.violations)

    def test_destructive_command_rejected_when_profile_denies(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="cleanup", command="rm -rf /tmp/build")],
                scope_check=ScopeCheck(description="cleanup check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("destructive" in v for v in result.violations)

    def test_command_with_redirection_rejected(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="log", command="echo ok > /tmp/ok.txt")],
                scope_check=ScopeCheck(description="log check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("forbidden shell token" in v for v in result.violations)

    def test_command_with_pipe_rejected(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="pipe", command="cat file | grep x")],
                scope_check=ScopeCheck(description="pipe check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("forbidden shell token" in v for v in result.violations)

    def test_command_with_backtick_rejected(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="expansion", command="echo `date`")],
                scope_check=ScopeCheck(description="expansion check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "forbidden shell token" in v or "forbidden pattern" in v
            for v in result.violations
        )

    def test_command_with_subshell_rejected(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="expansion", command="echo $(date)")],
                scope_check=ScopeCheck(description="expansion check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "forbidden shell token" in v or "forbidden pattern" in v
            for v in result.violations
        )

    def test_sudo_command_rejected(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="install", command="sudo apt update")],
                scope_check=ScopeCheck(description="install check"),
            )
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("privilege escalation" in v for v in result.violations)

    def test_docker_socket_allowed_when_profile_permits(self) -> None:
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[
                    Check(
                        type="docker",
                        command="docker -H unix:///var/run/docker.sock ps",
                    )
                ],
                scope_check=ScopeCheck(description="docker check"),
            )
        )
        profile = _make_profile(docker_socket="allow")

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True

    def test_destructive_shell_allowed_when_profile_permits(self) -> None:
        # ``rm -r`` (without ``-f``) is destructive but not a hard-forbidden
        # pattern; it is allowed when the project profile permits destructive
        # shell operations.
        contract = _make_contract(
            completion_contract=CompletionContract(
                task_id="task-1",
                required=[Check(type="cleanup", command="rm -r /tmp/build")],
                scope_check=ScopeCheck(description="cleanup check"),
            )
        )
        profile = _make_profile(destructive_shell="allow")

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True


class TestPolicyEngineVerificationCommandsAllowlist:
    def test_safe_verification_command_passes(self) -> None:
        contract = _make_contract(
            verification={"commands": ["uv run pytest -q"]}
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True

    def test_malicious_verification_command_rejected(self) -> None:
        contract = _make_contract(
            verification={"commands": ["echo ok; rm -rf /"]},
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any(
            "forbidden shell token" in v or "destructive shell" in v
            for v in result.violations
        )

    def test_verification_sudo_command_rejected(self) -> None:
        contract = _make_contract(
            verification={"commands": ["sudo apt update"]},
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("privilege escalation" in v for v in result.violations)

    def test_verification_docker_socket_command_rejected_when_denied(self) -> None:
        contract = _make_contract(
            verification={
                "commands": ["docker -H unix:///var/run/docker.sock ps"]
            },
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("docker socket" in v for v in result.violations)

    def test_verification_destructive_command_rejected_when_denied(self) -> None:
        contract = _make_contract(
            verification={"commands": ["rm -rf /tmp/build"]},
        )
        profile = _make_profile()

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is False
        assert any("destructive" in v for v in result.violations)

    def test_verification_commands_may_be_allowed_by_profile(self) -> None:
        # A destructive verification command is allowed when the project profile
        # explicitly permits destructive shell operations.
        contract = _make_contract(
            verification={"commands": ["rm -r /tmp/build"]},
        )
        profile = _make_profile(destructive_shell="allow")

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True


class TestPolicyEngineRoleAllowlist:
    def test_default_worker_role_on_opencode_passes(self) -> None:
        contract = _make_contract()
        assert contract.execution.role == "worker"
        profile = _make_profile(allowed_harnesses=["opencode"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True
        assert result.violations == []

    def test_allowed_planner_role_on_claude_code_passes(self) -> None:
        contract = _make_contract(
            harness="claude-code",
        )
        contract.execution.role = "planner"
        profile = _make_profile(allowed_harnesses=["claude-code"])

        result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

        assert result.allowed is True
        assert result.violations == []

    def test_unknown_role_on_registered_harness_is_rejected(self) -> None:
        # Use a custom, registered harness that does NOT allow planner to prove
        # allowed_roles enforcement is actually evaluated. The global registry is
        # restored afterwards.
        fake_harness = HarnessProvider(
            name="aider",
            command="aider",
            auth="provider-configured",
            supports_mcp=False,
            allowed_roles=["worker", "fallback"],
        )
        registry.register(fake_harness)
        try:
            contract = _make_contract(harness="aider")
            contract.execution.role = "planner"
            profile = _make_profile(allowed_harnesses=["aider"])

            result = PolicyEngine.evaluate(contract, profile, ApprovalType.EXECUTION)

            assert result.allowed is False
            assert any(
                "Role 'planner' is not allowed by harness 'aider'" in v
                for v in result.violations
            )
        finally:
            del registry._providers["aider"]
