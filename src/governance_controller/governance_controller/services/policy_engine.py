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


def _normalize_path(path: str) -> str:
    """Return a path with trailing slashes removed for prefix comparison."""
    return path.rstrip("/")


def _is_inside(path: str, forbidden: str) -> bool:
    """Return True if *path* is exactly *forbidden* or lives underneath it."""
    path = _normalize_path(path)
    forbidden = _normalize_path(forbidden)
    if path == forbidden:
        return True
    prefix = forbidden + "/"
    return path.startswith(prefix)


def _forbidden_path_conflicts(
    touched_paths: set[str], forbidden_paths: list[str]
) -> set[str]:
    """Return the subset of *touched_paths* that fall under any forbidden path."""
    conflicts: set[str] = set()
    for touched in touched_paths:
        for forbidden in forbidden_paths:
            if _is_inside(touched, forbidden):
                conflicts.add(touched)
                break
    return conflicts


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

        # 3. Forbidden path enforcement: any input or deliverable that the task
        #    explicitly touches must not be inside a path forbidden by the
        #    project profile. Exact matches are also rejected.
        touched_paths = set(contract.inputs + contract.deliverables)
        conflicts = _forbidden_path_conflicts(
            touched_paths, profile.security.forbidden_paths
        )
        for path in sorted(conflicts):
            violations.append(f"Task touches forbidden path: {path}")

        # 4. Security posture enforcement from project profile.
        security = profile.security
        execution = contract.execution
        if security.docker_socket == "deny" and execution.uses_docker_socket:
            violations.append("Docker socket access is denied by project profile")
        if security.destructive_shell == "deny" and execution.destructive_shell:
            violations.append(
                "Destructive shell commands are denied by project profile"
            )
        if security.spawn_subagents == "deny" and execution.spawn_subagents:
            violations.append("Spawning subagents is denied by project profile")
        if (
            security.network == "restricted"
            and execution.network_access == "unrestricted"
        ):
            violations.append(
                "Unrestricted network access is denied by project profile"
            )

        # 5. Approval type-driven checks.
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
        git = profile.git
        execution = contract.execution

        # Merge approval: require git.merge_requires_human to be enabled.
        if approval_type == ApprovalType.MERGE and not git.merge_requires_human:
            violations.append("Merge approval requires human merge gate in profile")

        # Merge approval: respect force_push and signed_commit project settings.
        if approval_type == ApprovalType.MERGE:
            if git.force_push == "deny" and execution.force_push:
                violations.append("Force push is denied by project profile")
            if git.signed_commits == "required" and not execution.signed_commits:
                violations.append("Signed commits are required by project profile")
