"""Embedded policy engine for task approvals.

Command-shell allowlist
-----------------------
CompletionContract checks run system commands during verification, so PolicyEngine
validates every ``Check.command`` before approval. The checks below are applied in
``_check_completion_contract_commands``:

1. Reject shell metacharacters/operators that can change semantics or chain
   arbitrary commands: ``;``, ``&``, ``|``, ``&&``, ``||``, ``>``, ``<``, ``>>``,
   ``<<``, ``*`` (glob), ``?`` (glob), backticks, ``$(...)``, and ``${...}``.
2. Reject sub-shell / process substitution patterns: ``$(``, ``${``, backticks.
3. Reject privilege escalation: ``sudo``, ``su -``, ``doas``.
4. Reject common destructive file-system operations: ``rm -rf``, ``rm -fr``,
   ``rm --no-preserve-root``, ``dd if=... of=...`` with device-ish targets,
   ``mkfs.*``, ``>`` redirections that could truncate files.
5. Reject commands that require Docker socket access (``docker.sock``,
   ``/var/run/docker.sock``) unless the project profile permits it.
6. Reject any command that sets ``uses_docker_socket`` or ``destructive_shell`` in
   the ExecutionConfig unless the corresponding project profile security field
   explicitly allows it.

This is an explicit allowlist approach: if a command matches any forbidden pattern,
approval is denied with a human-readable violation. Commands that are meant to be
high-privilege must be declared by the task proposer and allowed by the project
profile before they can pass policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from governance_controller.constants import ApprovalType
from governance_controller.harness import registry
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.utils.paths import normalize_path


class PolicyViolationError(Exception):
    """Raised when policy evaluation rejects an approval request.

    Carries the original human-readable message and a structured list of
    violations so callers do not have to parse comma-joined strings.
    """

    def __init__(self, message: str, violations: list[str]) -> None:
        super().__init__(message)
        self.message = message
        self.violations = violations


@dataclass
class PolicyResult:
    """Result of policy evaluation."""

    allowed: bool
    violations: list[str]


# Forbidden shell tokens/operators/metacharacters. These can alter command
# semantics, chain arbitrary commands, glob widely, or perform redirections.
_FORBIDDEN_SHELL_TOKENS: set[str] = {
    ";",
    "&",
    "|",
    "&&",
    "||",
    ">",
    "<",
    ">>",
    "<<",
    "*",
    "?",
    "`",
    "$(",
    "${",
    "}",
}

# Privilege-escalation substrings.
_FORBIDDEN_PRIVILEGE_SUBSTRINGS: tuple[str, ...] = ("sudo", "su -", "doas")

# Destructive file-system substrings / patterns.
_FORBIDDEN_DESTRUCTIVE_SUBSTRINGS: tuple[str, ...] = (
    "rm -rf",
    "rm -fr",
    "rm --no-preserve-root",
    "mkfs.",
    "dd if=",
)

# Docker-socket access substrings.
_FORBIDDEN_DOCKER_SOCKET_SUBSTRINGS: tuple[str, ...] = (
    "docker.sock",
    "/var/run/docker.sock",
)


_FORBIDDEN_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    # Sub-shell / process substitution
    re.compile(r"\$\s*\("),
    re.compile(r"`[^`]*`"),
]


def _normalize_path(path: str) -> str:
    """Return a normalized path for prefix comparison."""
    return normalize_path(path)


def _is_inside(path: str, forbidden: str) -> bool:
    """Return True if *path* is exactly *forbidden* or lives underneath it."""
    normalized_path = _normalize_path(path)
    normalized_forbidden = _normalize_path(forbidden)
    if normalized_path == normalized_forbidden:
        return True
    prefix = normalized_forbidden + "/"
    return normalized_path.startswith(prefix)


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


def _contains_forbidden_shell_token(command: str) -> bool:
    """Return True if *command* contains a forbidden shell token/operator."""
    # Check the raw command string for exact token presence; this catches
    # simple redirections, globs, and command chaining.
    return any(token in command for token in _FORBIDDEN_SHELL_TOKENS)


def _has_forbidden_pattern(command: str) -> bool:
    """Return True if *command* matches a forbidden regex pattern."""
    return any(pattern.search(command) for pattern in _FORBIDDEN_COMMAND_PATTERNS)


def _has_forbidden_substrings(command: str, substrings: tuple[str, ...]) -> bool:
    """Return True if any forbidden substring is present (case-insensitive)."""
    lowered = command.lower()
    return any(sub in lowered for sub in substrings)


def _normalize_and_validate_command(command: str) -> tuple[bool, list[str]]:
    """Return (ok, violations) for a single Check.command string."""
    local_violations: list[str] = []

    if _contains_forbidden_shell_token(command):
        local_violations.append(
            f"Command contains forbidden shell token/operator: {command!r}"
        )

    if _has_forbidden_pattern(command):
        local_violations.append(
            f"Command contains forbidden pattern (sub-shell/backticks): {command!r}"
        )

    if _has_forbidden_substrings(command, _FORBIDDEN_PRIVILEGE_SUBSTRINGS):
        local_violations.append(
            f"Command contains privilege escalation: {command!r}"
        )

    if _has_forbidden_substrings(command, _FORBIDDEN_DESTRUCTIVE_SUBSTRINGS):
        local_violations.append(
            f"Command contains destructive shell operation: {command!r}"
        )

    return not local_violations, local_violations


def _validate_command_against_profile(
    command: str,
    profile: ProjectProfile,
) -> list[str]:
    """Validate a single command and cross-check inferred capabilities.

    Applies ``_normalize_and_validate_command`` plus docker-socket and
    destructive-shell profile cross-checks, mirroring the enforcement used
    for CompletionContract commands.
    """
    violations: list[str] = []
    ok, check_violations = _normalize_and_validate_command(command)
    if not ok:
        violations.extend(check_violations)
        return violations

    # Cross-check inferred docker-socket usage against profile.
    if _has_forbidden_substrings(
        command, _FORBIDDEN_DOCKER_SOCKET_SUBSTRINGS
    ) and profile.security.docker_socket == "deny":
        violations.append(
            "Command references docker socket but profile denies "
            f"docker_socket: {command!r}"
        )

    # Cross-check inferred destructive shell usage against profile.
    if (
        _is_destructive_command(command)
        and profile.security.destructive_shell == "deny"
    ):
        violations.append(
            "Command is destructive but profile denies "
            f"destructive_shell: {command!r}"
        )

    return violations


def _validate_completion_contract_commands(
    contract: TaskContract,
    profile: ProjectProfile,
) -> list[str]:
    """Validate all CompletionContract commands for forbidden shell patterns.

    Also cross-checks commands that imply docker-socket or destructive-shell
    usage against the project profile's security posture.
    """
    violations: list[str] = []
    completion = contract.completion_contract
    if completion is None:
        return violations

    for check in list(completion.required) + list(completion.optional):
        violations.extend(_validate_command_against_profile(check.command, profile))

    return violations


def _validate_verification_commands(
    contract: TaskContract,
    profile: ProjectProfile,
) -> list[str]:
    """Validate ``TaskContract.verification["commands"]`` for shell patterns.

    These commands are merged with CompletionContract checks at verification
    execution time, so they must pass the same policy gates before approval.
    """
    violations: list[str] = []
    verification = contract.verification
    if not verification:
        return violations

    commands = verification.get("commands", [])
    if not isinstance(commands, list):
        return violations

    for command in commands:
        if isinstance(command, str):
            violations.extend(
                _validate_command_against_profile(command, profile)
            )

    return violations


def _is_destructive_command(command: str) -> bool:
    """Return True if *command* looks like a destructive file operation."""
    lowered = command.lower()
    # Classic recursive removal.
    if "rm -r" in lowered or "rm -f" in lowered:
        return True
    # Formatting or direct block-device writes.
    if "mkfs." in lowered or "dd if=" in lowered:
        return True
    # Explicit output redirection that can truncate files.
    return ">" in command


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

        # 2 & 5. Harness allowlist and role allowlist for that harness.
        requested_harness = contract.execution.harness
        allowed_harnesses = profile.execution.allowed_harnesses
        if requested_harness not in allowed_harnesses:
            violations.append(
                f"Harness '{requested_harness}' is not in the allowed harness list"
            )

        if registry.is_registered(requested_harness):
            provider = registry.get(requested_harness)
            requested_role = contract.execution.role
            if requested_role not in provider.allowed_roles:
                violations.append(
                    f"Role '{requested_role}' is not allowed by harness "
                    f"'{requested_harness}'"
                )
        else:
            violations.append(
                f"Harness '{requested_harness}' is not registered; cannot validate role"
            )

        # 3. Forbidden path enforcement: any input or deliverable that the task
        #    explicitly touches must not be inside a path forbidden by the
        #    project profile or by the task contract itself. Exact matches are
        #    also rejected.
        touched_paths = set(contract.inputs + contract.deliverables)
        forbidden_paths = list(
            set(profile.security.forbidden_paths) | set(contract.forbidden_paths)
        )
        conflicts = _forbidden_path_conflicts(touched_paths, forbidden_paths)
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

        # 4b. Execution resource caps from project profile.
        if execution.timeout_minutes > profile.execution.timeout_minutes:
            violations.append(
                f"Task timeout_minutes ({execution.timeout_minutes}) "
                f"exceeds project cap ({profile.execution.timeout_minutes})"
            )
        if execution.max_retries > profile.execution.max_retries:
            violations.append(
                f"Task max_retries ({execution.max_retries}) "
                f"exceeds project cap ({profile.execution.max_retries})"
            )

        # 5. Completion-contract command allowlist. Any shell command scheduled
        #    to run during verification must be reviewed for forbidden tokens
        #    and destructive operations.
        violations.extend(_validate_completion_contract_commands(contract, profile))

        # 5b. TaskContract.verification commands allowlist. These commands are
        #     merged with CompletionContract checks by VerificationService, so
        #     they are subject to the same policy gates.
        violations.extend(_validate_verification_commands(contract, profile))

        # 6. Approval type-driven checks.
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
