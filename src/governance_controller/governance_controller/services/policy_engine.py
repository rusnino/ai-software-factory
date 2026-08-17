"""Embedded policy engine for task approvals."""

from dataclasses import dataclass

from governance_controller.constants import ApprovalType
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract


@dataclass
class PolicyResult:
    """Result of policy evaluation."""

    allowed: bool
    violations: list[str]


class PolicyEngine:
    """Evaluate policy rules against a TaskContract and ProjectProfile."""

    @classmethod
    def evaluate(
        cls,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
    ) -> PolicyResult:
        """Evaluate all policy rules for the given inputs.

        Args:
            contract: The task contract under evaluation.
            profile: The project profile containing constraints.
            approval_type: The kind of approval being requested.

        Returns:
            A PolicyResult indicating whether the approval is allowed and any
            human-readable violations.
        """
        violations: list[str] = []

        # 1. Task Contract completeness
        if not contract.objective.strip():
            violations.append("Task contract objective is empty")
        if not contract.acceptance:
            violations.append("Task contract acceptance criteria are empty")

        # 2 & 5. Harness allowlist
        requested_harness = contract.execution.harness
        allowed_harnesses = profile.execution.allowed_harnesses
        if requested_harness not in allowed_harnesses:
            violations.append(
                f"Harness '{requested_harness}' is not in the allowed harness list"
            )

        # 3. Forbidden path conflicts
        contract_forbidden = set(contract.forbidden_paths)
        profile_forbidden = set(profile.security.forbidden_paths)
        conflicts = sorted(contract_forbidden & profile_forbidden)
        for path in conflicts:
            violations.append(f"Forbidden path conflict: {path}")

        # 4. Required human approval is embedded via approval_type gating.
        # Approval type-driven checks are handled by the state machine and
        # approval service mappings, so no explicit rule is needed here.
        cls._check_approval_type_rules(contract, profile, approval_type, violations)

        return PolicyResult(allowed=not violations, violations=violations)

    @classmethod
    def _check_approval_type_rules(
        cls,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
        violations: list[str],
    ) -> None:
        """Apply any approval-type-specific policy rules."""
        # Merge approval: require git.merge_requires_human to be enabled.
        if approval_type == ApprovalType.MERGE and not profile.git.merge_requires_human:
            violations.append("Merge approval requires human merge gate in profile")
