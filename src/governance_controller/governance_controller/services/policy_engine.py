"""Embedded policy engine for task approvals.

Command-shell allowlist
-----------------------
CompletionContract checks run system commands during verification, so PolicyEngine
validates every ``Check.command`` before approval. The checks below are applied in
``_check_completion_contract_commands``:

1. Parse the command with :func:`shlex.split` to obtain the resolved argv that
   the shell would actually execute.
2. Reject commands that cannot be parsed or that still contain shell
   metacharacters/operators after parsing (redirections, pipes, command
   substitution, globbing, variable expansion, etc.).
3. Require an explicit allowlist match for ``argv[0]``. Only a small set of
   common verification binaries is permitted; wrappers and interpreters such as
   ``bash -c``, ``env``, ``xargs``, ``nice``, ``nohup``, ``ssh`` and similar are
   not allowed because their payloads bypass token-level policy checks.
4. Reject common destructive file-system operations inside the resolved argv:
   ``rm`` with recursive and force flags, ``rm --no-preserve-root``,
   ``dd if=... of=...`` with device-ish targets, ``mkfs.*``.
5. Reject commands that reference Docker socket paths
   (``docker.sock``, ``/var/run/docker.sock``) unless the project profile
   permits it.
    6. Reject any command that sets ``uses_docker_socket`` or ``destructive_shell``
       in the ExecutionConfig unless the corresponding project profile security
       field explicitly allows it.

    7. Reject network fetch tools whose payloads cannot be audited at the argv
       level (``curl`` and ``wget`` removed in Phase 1; see #142).

This is an explicit allowlist approach: if a command matches any forbidden
pattern, approval is denied with a human-readable violation. Commands that are
meant to be high-privilege must be declared by the task proposer and allowed by
the project profile before they can pass policy.

.. rubric:: Known Phase 1 limitations

- ``network_access`` is a self-declared field. The engine compares the declared
  value against ``profile.security.network`` but does not parse command text to
  enforce egress domains or block cloud-metadata endpoints such as
  ``169.254.169.254`` (#143).
- Build tools (``make``, ``npm``, ``pip``, ``cargo``, etc.) execute code declared
  in the worktree files they read. This is an inherent, argv-level-unmitigable
  risk that can only be contained by the execution sandbox (#147).
"""

from __future__ import annotations

import shlex
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


# Forbidden shell metacharacters/operators that can change semantics, chain
# arbitrary commands, glob widely, perform redirections, or expand variables.
# These are rejected even when they appear inside a single shlex token.
_FORBIDDEN_SHELL_METACHARACTERS: set[str] = {
    ";",
    "&",
    "|",
    ">",
    "<",
    "`",
    "$",
    "(",
    ")",
    "{",
    "}",
    "*",
    "?",
    "[",
    "]",
    "~",
    "#",
}

# Control characters that can terminate statements or smuggle payloads even
# when shlex splits the surrounding text into innocent-looking tokens.
_FORBIDDEN_CONTROL_CHARACTERS: set[str] = {"\n", "\r", "\x00"}

# Allowed base commands for verification/completion-contract shell checks.
# Only these argv[0] values are permitted. Wrappers and interpreters (bash -c,
# env, xargs, nice, nohup, ssh, timeout, etc.) are excluded because they can
# carry an arbitrary payload that token-level policy checks would not inspect.
#
# NOTE: ``python``/``python3`` are deliberately omitted from this allowlist
# even though they are common verification tools. They are listed in
# ``_FORBIDDEN_WRAPPER_COMMANDS`` and that check runs *before* the allowlist,
# so any ``python ...`` command is rejected as a wrapper/interpreter (e.g.
# ``python -c`` is arbitrary code execution). Prefixing with an allowlisted
# runner such as ``uv`` (``uv run python -m pytest``) remains permitted.
_ALLOWED_VERIFICATION_COMMANDS: frozenset[str] = frozenset(
    {
        # Package managers / build tools
        "apt",
        "apt-get",
        "brew",
        "cargo",
        "cmake",
        "composer",
        "conan",
        "dotnet",
        "dpkg",
        "gem",
        "gradle",
        "make",
        "meson",
        "mix",
        "mvn",
        "npm",
        "npx",
        "nuget",
        "pip",
        "pip3",
        "pnpm",
        "poetry",
        "raco",
        "rake",
        "sbt",
        "stack",
        "uv",
        "yarn",
        # VCS
        "git",
        "hg",
        "svn",
        # Verification / test runners
        "pytest",
        "tox",
        "nox",
        "jest",
        "mocha",
        "go",
        "gotestsum",
        "prove",
        "rspec",
        "unittest",
        "vitest",
        # Linters / formatters / type checkers
        "bandit",
        "black",
        "flake8",
        "mypy",
        "pylint",
        "pyright",
        "ruff",
        # Shell utilities
        "cat",
        "cp",
        # Network fetch tools removed: curl/wget config files (-K/--config)
        # and file:// schemes allow local file read/write/exfiltration that is
        # not enumerable at the argv level in Phase 1. See #142.
        "cut",
        "date",
        "diff",
        "echo",
        "find",
        "grep",
        "head",
        "id",
        "ls",
        "mkdir",
        "mv",
        "pwd",
        "rm",
        "sed",
        "sort",
        "tail",
        "tar",
        "tee",
        "test",
        "touch",
        "tr",
        "uniq",
        "unzip",
        "wc",
        "which",
        "whoami",
        "zip",
        # Container tools are intentionally omitted. docker/podman/kubectl
        # allow bind mounts, privileged mode, and host namespace sharing that
        # cannot be safely whitelisted at the argv level in Phase 1.
    }
)

# Privilege-escalation base commands (checked against every argv token).
_FORBIDDEN_PRIVILEGE_COMMANDS: frozenset[str] = frozenset({"sudo", "su", "doas"})

# Destructive file-system base commands (checked against every argv token).
_FORBIDDEN_DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({"mkfs"})

# Wrappers/interpreters whose payload would be executed by the shell but is not
# part of the token-level argv we inspect. These are rejected at argv[0] so an
# opaque payload cannot bypass policy.
_FORBIDDEN_WRAPPER_COMMANDS: frozenset[str] = frozenset(
    {
        "bash",
        "dash",
        "sh",
        "zsh",
        "ssh",
        "env",
        "nice",
        "nohup",
        "timeout",
        "xargs",
        "command",
        "exec",
        "eval",
        "busybox",
        "install",
        "perl",
        "python",
        "python3",
        "ruby",
        "node",
        "php",
        "lua",
    }
)

# Docker-socket access substrings (checked against resolved argv tokens).
# NOTE: docker/podman/kubectl are intentionally absent from
# ``_ALLOWED_VERIFICATION_COMMANDS`` because bind-mount and privileged flags
# cannot be safely enumerated in Phase 1. These substrings remain for legacy
# socket-reference detection on any other command that might mention them.
_FORBIDDEN_DOCKER_SOCKET_SUBSTRINGS: tuple[str, ...] = (
    "docker.sock",
    "/var/run/docker.sock",
)

# Container flags that break isolation. Docker/podman/kubectl are not in the
# allowlist; these patterns are kept as a defense-in-depth scan on any command
# that somehow mentions them (e.g. wrapped by a future allowed helper).
_FORBIDDEN_CONTAINER_ESCAPE_FLAGS: frozenset[str] = frozenset(
    {
        "--privileged",
        "--pid=host",
        "--network=host",
        "--ipc=host",
        "--uts=host",
        "--cap-add",
        "--security-opt",
        "--volume",
        "-v",
        "--mount",
    }
)

# Dangerous git config keys that accept arbitrary shell commands. #139: any
# git -c override matching these keys is rejected, because values such as
# core.sshCommand=<shell command> execute unconditionally when git touches SSH.
_FORBIDDEN_GIT_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "core.sshcommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "credential.helper",
        "include.path",
    }
)

# Dangerous tar flags that execute arbitrary commands or delete files.
# #140: --to-command runs an arbitrary shell command per extracted member.
_FORBIDDEN_TAR_FLAGS: frozenset[str] = frozenset(
    {
        "--to-command",
        "--to-command=",
        "--remove-files",
        "--remove-file",
    }
)

# Dangerous find predicates and actions. #135: -delete silently removes files;
# -exec and -ok can run arbitrary commands.
_FORBIDDEN_FIND_ACTIONS: frozenset[str] = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fls",
        "-fprint",
        "-fprint0",
    }
)


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


def _extract_command_paths(command: str) -> set[str]:
    """Return path-like tokens from *command*'s resolved argv.

    A token is treated as a path if it looks like an absolute path, a
    relative path segment containing ``/``, a tilde expansion, or a path
    argument glued to a short option such as ``-I/some/path``. This is a
    heuristic; it intentionally skips plain flag tokens like ``-l``.
    """
    argv, _ = _parse_command_to_argv(command)
    if argv is None:
        return set()
    paths: set[str] = set()
    for token in argv[1:]:
        lowered = token.lower()
        if token.startswith("-"):
            # Some flags carry an inline path: -I/path, --file=/path, -I=path.
            for sep in ("=", ""):
                for flag_prefix in ("-I", "--include", "--exclude", "--file"):
                    prefix = flag_prefix + sep
                    if lowered.startswith(prefix.lower()):
                        candidate = token[len(prefix) :]
                        if candidate:
                            paths.add(candidate)
                            break
            continue
        # Keep tokens that resemble filesystem paths.
        if token.startswith(("/", "~", ".")) or "/" in token:
            paths.add(token)
    return paths


def _parse_command_to_argv(command: str) -> tuple[list[str] | None, str | None]:
    """Parse *command* into the argv the shell would actually execute.

    Returns ``(argv, None)`` on success, or ``(None, error_message)`` when the
    command is unparsable or contains embedded NUL bytes.
    """
    if "\x00" in command:
        return None, "command contains embedded NUL byte"
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, f"command could not be parsed: {exc}"
    if not argv:
        return None, "command is empty after parsing"
    return argv, None


def _argv_contains_metacharacter(argv: list[str]) -> bool:
    """Return True if any token still contains a forbidden shell metacharacter.

    shlex.split resolves quotes and backslash escapes, so a token like
    ``r\\m`` becomes ``rm``. If any token still contains ``$``, ``|``, ``>``,
    etc., the command relies on shell behavior that the verification harness
    does not support and that could be used to bypass policy.
    """
    for token in argv:
        if any(ch in token for ch in _FORBIDDEN_SHELL_METACHARACTERS):
            return True
    return False


def _base_command(token: str) -> str:
    """Return the lower-cased base command name for a token."""
    return token.split("/")[-1].lower()


def _is_allowed_argv0(argv: list[str]) -> bool:
    """Return True if argv[0] is in the explicit allowlist."""
    return _base_command(argv[0]) in _ALLOWED_VERIFICATION_COMMANDS


def _is_forbidden_wrapper(argv: list[str]) -> bool:
    """Return True if argv[0] is a known wrapper/interpreter."""
    return _base_command(argv[0]) in _FORBIDDEN_WRAPPER_COMMANDS


def _is_privilege_escalation(argv: list[str]) -> bool:
    """Return True if any token is a known privilege-escalation command."""
    return any(
        _base_command(token) in _FORBIDDEN_PRIVILEGE_COMMANDS for token in argv
    )


def _is_docker_socket_command(argv: list[str]) -> bool:
    """Return True if any resolved token references the Docker socket."""
    lowered = [token.lower() for token in argv]
    return any(
        sub in token for token in lowered for sub in _FORBIDDEN_DOCKER_SOCKET_SUBSTRINGS
    )


def _is_container_escape_flag(argv: list[str]) -> bool:
    """Return True if any token is a container flag that breaks isolation."""
    return any(
        any(
            token == flag or token.startswith(flag + "=")
            for flag in _FORBIDDEN_CONTAINER_ESCAPE_FLAGS
        )
        for token in argv
    )


def _has_git_dangerous_config(argv: list[str]) -> bool:
    """Return True if a git -c override sets a dangerous config key."""
    if _base_command(argv[0]) != "git":
        return False
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in ("-c", "--config"):
            if i + 1 >= len(argv):
                return False
            key, _, _ = argv[i + 1].partition("=")
            if key.lower().strip() in _FORBIDDEN_GIT_CONFIG_KEYS:
                return True
            i += 2
            continue
        if token.lower().startswith("-c"):
            config = token[2:]
            key, _, _ = config.partition("=")
            if key.lower().strip() in _FORBIDDEN_GIT_CONFIG_KEYS:
                return True
        i += 1
    return False


def _has_tar_dangerous_flag(argv: list[str]) -> bool:
    """Return True if tar uses an extraction hook or destructive flag."""
    if _base_command(argv[0]) != "tar":
        return False
    for token in argv[1:]:
        lowered = token.lower()
        if any(
            lowered == flag or lowered.startswith(flag + "=")
            for flag in _FORBIDDEN_TAR_FLAGS
        ):
            return True
    return False


def _has_find_dangerous_action(argv: list[str]) -> bool:
    """Return True if find uses a destructive or command-execution action."""
    if _base_command(argv[0]) != "find":
        return False
    i = 1
    while i < len(argv):
        token = argv[i]
        lowered = token.lower()
        if lowered in _FORBIDDEN_FIND_ACTIONS:
            return True
        if lowered in ("-exec", "-execdir", "-ok", "-okdir"):
            # Skip the command body and the terminating ; or + so we do not
            # misinterpret an argument to the inner command as a find action.
            i += 1
            while i < len(argv) and argv[i] not in (";", "+"):
                i += 1
            if i < len(argv):
                i += 1
            continue
        i += 1
    return False


def _has_git_clean_destructive(argv: list[str]) -> bool:
    """Return True if git clean is invoked with force/remove flags."""
    if _base_command(argv[0]) != "git":
        return False
    if len(argv) < 2 or argv[1] != "clean":
        return False
    for token in argv[2:]:
        if token in {"-f", "--force", "-x", "-d"}:
            return True
        if token.startswith("-") and len(token) > 1 and any(
            ch in token for ch in "fxd"
        ):
            return True
    return False


def _has_sed_dangerous_flag(argv: list[str]) -> bool:
    """Return True if sed uses the GNU `e` command or `s///e` flag.

    GNU sed's `e` address-command (`<addr>e <shell-command>`) and the
    `s///e` substitution flag both execute arbitrary shell commands. They
    are rejected regardless of quoting because they are unconditional RCE.
    """
    if _base_command(argv[0]) != "sed":
        return False
    script_tokens: list[str] = []
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in ("-e", "--expression", "-f", "--file"):
            i += 1
            if i < len(argv):
                script_tokens.append(argv[i])
        elif token.startswith("-e"):
            script_tokens.append(token[2:])
        elif not token.startswith("-"):
            script_tokens.append(token)
        i += 1
    for script in script_tokens:
        # Detect s///e, s/.../.../e, and s@...@...@e etc.
        # The flag 'e' must appear after the final delimiter; for safety we
        # reject any substitution followed by 'e' as a trailing flag.
        if script.startswith("s"):
            delim = script[1:2]
            if delim and script.rstrip(delim).endswith("e"):
                return True
        # Detect the bare 'e' command, e.g. '1e id', '$e touch /tmp/x'.
        # Look for an address prefix (line number, '$', or '%') immediately
        # followed by 'e ' as the sed command.
        import re

        if re.search(r"(^|[;\n\s])([0-9]+|\$|%)?e\s", script):
            return True
    return False


def _is_dd_to_device(argv: list[str]) -> bool:
    """Return True for ``dd if=... of=/dev/...`` or block-device-like targets."""
    if _base_command(argv[0]) != "dd":
        return False
    has_input = False
    has_device_output = False
    for token in argv[1:]:
        if token.lower().startswith("if="):
            has_input = True
        if token.lower().startswith("of=") and (
            token.startswith("of=/dev/") or token.startswith("of=/")
        ):
            has_device_output = True
    return has_input and has_device_output


def _is_recursive_force_rm(argv: list[str]) -> bool:
    """Return True for ``rm`` with both recursive and force flags."""
    if _base_command(argv[0]) != "rm":
        return False
    recursive = False
    force = False
    for token in argv[1:]:
        if token == "--no-preserve-root":
            return True
        if token.startswith("-") and len(token) > 1:
            if "r" in token or "R" in token:
                recursive = True
            if "f" in token:
                force = True
    return recursive and force


def _is_destructive_command(argv: list[str]) -> bool:
    """Return True if *argv* looks like a destructive file operation."""
    return bool(
        any(
            _base_command(token) in _FORBIDDEN_DESTRUCTIVE_COMMANDS
            for token in argv
        )
        or _is_recursive_force_rm(argv)
        or _is_dd_to_device(argv)
        or _has_find_dangerous_action(argv)
        or _has_tar_dangerous_flag(argv)
        or _has_git_clean_destructive(argv)
    )


def _has_command_execution_primitive(argv: list[str]) -> bool:
    """Return True if an allowlisted binary carries a command-execution hook."""
    return bool(
        _has_git_dangerous_config(argv)
        or _has_tar_dangerous_flag(argv)
        or _has_find_dangerous_action(argv)
        or _has_sed_dangerous_flag(argv)
    )


def _normalize_and_validate_command(command: str) -> tuple[bool, list[str]]:
    """Return (ok, violations) for a single Check.command string.

    The command is parsed into argv with :func:`shlex.split` and validated
    against forbidden shell metacharacters, privilege escalation, and
    destructive operations. This catches obfuscations such as
    ``r\\m -\\rf /tmp`` (backslash splitting) because shlex resolves them to
    ``['rm', '-rf', '/tmp']`` before policy checks run. Variable-expansion
    tricks such as ``rm$IFS-rf`` are rejected because ``$`` is not permitted in
    any token.
    """
    local_violations: list[str] = []

    if any(ch in command for ch in _FORBIDDEN_CONTROL_CHARACTERS):
        local_violations.append(
            f"Command contains forbidden shell token/operator: {command!r}"
        )
        return False, local_violations

    argv, error = _parse_command_to_argv(command)
    if argv is None:
        local_violations.append(
            f"Command is malformed and cannot be validated: {command!r} ({error})"
        )
        return False, local_violations

    if _argv_contains_metacharacter(argv):
        local_violations.append(
            f"Command contains forbidden shell token/operator: {command!r}"
        )

    if _is_forbidden_wrapper(argv):
        local_violations.append(
            f"Command uses a wrapper/interpreter that can hide payloads: "
            f"{command!r}"
        )
    elif not _is_allowed_argv0(argv):
        local_violations.append(
            f"Command argv[0] is not in the verification allowlist: {command!r}"
        )

    if _is_privilege_escalation(argv):
        local_violations.append(
            f"Command contains privilege escalation: {command!r}"
        )

    if _is_destructive_command(argv):
        local_violations.append(
            f"Command contains destructive shell operation: {command!r}"
        )

    if _has_command_execution_primitive(argv):
        local_violations.append(
            f"Command contains a command-execution primitive: {command!r}"
        )

    if _is_container_escape_flag(argv):
        local_violations.append(
            f"Command contains container isolation escape flag: {command!r}"
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

    argv, _ = _parse_command_to_argv(command)
    if argv is None:
        return violations

    # Cross-check inferred docker-socket usage against profile.
    if _is_docker_socket_command(argv) and profile.security.docker_socket == "deny":
        violations.append(
            "Command references docker socket but profile denies "
            f"docker_socket: {command!r}"
        )

    # Cross-check inferred destructive shell usage against profile.
    if _is_destructive_command(argv) and profile.security.destructive_shell == "deny":
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

        # 3. Forbidden path enforcement: any input, deliverable, or path-like
        #    argument appearing in a verification command must not be inside a
        #    path forbidden by the project profile or by the task contract itself.
        #    Exact matches are also rejected.
        touched_paths = set(contract.inputs + contract.deliverables)
        completion = contract.completion_contract
        verification = contract.verification or {}
        verification_commands: list[str] = []
        if isinstance(verification.get("commands"), list):
            verification_commands = [
                c for c in verification["commands"] if isinstance(c, str)
            ]
        command_paths: set[str] = set()
        for cmd_source in verification_commands:
            command_paths |= _extract_command_paths(cmd_source)
        if completion is not None:
            for check in list(completion.required) + list(completion.optional):
                command_paths |= _extract_command_paths(check.command)
        touched_paths |= command_paths

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
