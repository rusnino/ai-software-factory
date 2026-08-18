"""Tests for the embedded PolicyEngine."""

import pytest

from governance_controller.constants import ApprovalType
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
) -> TaskContract:
    data: dict = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "proposed_by": "agent-1",
        "objective": objective,
        "execution": ExecutionConfig(harness=harness),
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
    return TaskContract(**data)


def _make_profile(
    *,
    allowed_harnesses: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    merge_requires_human: bool = True,
) -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": allowed_harnesses or ["opencode"]},
        security={"forbidden_paths": forbidden_paths or []},
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
