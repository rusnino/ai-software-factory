"""Embedded policy engine for task approvals.

Command-shell allowlist
-----------------------
CompletionContract checks run system commands during verification, so PolicyEngine
validates every ``Check.command`` before approval. The checks below are applied in
``_validate_completion_contract_commands``:

1. Parse the command with :func:`shlex.split` to obtain the resolved argv that
   the shell would actually execute.
2. Reject commands that cannot be parsed or that still contain shell
   metacharacters/operators after parsing (redirections, pipes, command
   substitution, globbing, variable expansion, etc.).
3. Reject common wrappers and interpreters at ``argv[0]`` (``bash -c``,
    ``env``, ``xargs``, ``nice``, ``nohup``, ``ssh``, ``python``, ``node``, etc.)
    and nested interpreters launched by ``uv run`` because their payloads bypass
    token-level policy checks.
4. Require an explicit allowlist match for ``argv[0]``. Only a small set of
   common verification binaries is permitted.
 5. Reject common destructive file-system operations inside the resolved argv:
    ``rm`` with recursive and force flags, ``rm --no-preserve-root``,
    ``dd if=... of=...`` with device-ish targets, ``mkfs.*``,
    ``find -delete``/``-exec``/``-ok``/``-fprintf``,
    ``tar --to-command``/``--remove-files``/``--absolute-names``/
    ``--transform``/``--xform``, and ``git clean -f``/``-x``/``-d``.
6. Reject command-execution primitives on allowlisted binaries: ``git -c``
     overrides and persistent ``git config`` for dangerous config keys
     (``core.sshCommand``, ``core.fsmonitor``, ``core.editor``,
     ``core.hooksPath``, ``protocol.ext.allow``, ``credential.helper``, etc.),
     shell aliases, upload-pack overrides, and ``ext::`` transports; also
     ``git --config-env``, protected ``.git`` control-directory writes, tar
     extraction, and sed file I/O/``s///e``/``<addr>e`` primitives.
7. Reject container isolation escape flags (``--privileged``,
   ``--network=host``, ``--volume``, ``--mount``, etc.) as defense-in-depth in
   case a future allowed helper wraps a container binary. ``docker``/``podman``/
   ``kubectl`` themselves are already absent from the verification allowlist.
   The bare ``-v`` flag is intentionally excluded because it is overloaded by
   common allowlisted utilities such as ``grep -v``.
8. Reject commands that reference Docker socket paths
   (``docker.sock``, ``/var/run/docker.sock``) unless the project profile
   explicitly allows docker-socket access. Because the container tools are
   removed from the allowlist, this check now only fires for non-container
   commands that mention the socket path (e.g. ``curl --unix-socket ...``).
9. Reject any command that sets ``uses_docker_socket`` or ``destructive_shell``
   in the ExecutionConfig unless the corresponding project profile security
   field explicitly allows it.
10. Reject network fetch tools whose payloads cannot be audited at the argv
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

import re
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
# runner such as ``uv`` remains permitted for direct tools (``uv run pytest``),
# but not for nested interpreters.
_ALLOWED_VERIFICATION_COMMANDS: frozenset[str] = frozenset(
    {
        # Build tools / package managers.
        # Note: apt, apt-get, and dpkg are intentionally omitted. Installing a
        # local .deb executes arbitrary maintainer scripts; this is not safely
        # enumerable at the argv level in Phase 1. See #144.
        "brew",
        "cargo",
        "cmake",
        "composer",
        "conan",
        "dotnet",
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
        # unzip is intentionally omitted: extraction writes untrusted archive
        # members and cannot be made safe with this argv-level policy.
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

_UV_RUN_CHILD_COMMANDS: frozenset[str] = frozenset(
    {"bandit", "black", "flake8", "mypy", "pylint", "pyright", "pytest", "ruff"}
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
        "core.hookspath",
        "credential.helper",
        "include.path",
        "protocol.ext.allow",
    }
)

# Any command that can persist data in these repository control files is denied.
# A later git command can execute values read from them, so checking only git's
# own argv is insufficient.
_GIT_CONTROL_FILE_WRITERS: frozenset[str] = frozenset(
    {
        "cp",
        "mv",
        "mkdir",
        "tee",
        "touch",
        "tar",
        "unzip",
        "zip",
        "sed",
        "go",
        "npm",
        "pytest",
    }
)

# Options whose values select a filesystem location. The values are included in
# touched_paths even when the option and path are separate argv tokens.
_PATH_ARGUMENT_OPTIONS: frozenset[str] = frozenset(
    {
        "--directory",
        "--target-directory",
        "--files-from",
        "--git-dir",
        "--output",
        "--work-tree",
        "--file",
        "--include",
        "--exclude",
        "-C",
        "-I",
        "-f",
        "-t",
        "-T",
    }
)
_COMMAND_PATH_ARGUMENT_OPTIONS: dict[str, frozenset[str]] = {
    "git": frozenset({"-o"}),
    "go": frozenset({"-o"}),
    "pytest": frozenset(
        {
            "--basetemp",
            "--junitxml",
            "--junit-xml",
            "--result-log",
            "--log-file",
            "--debug",
        }
    ),
    "npm": frozenset(
        {"--prefix", "--userconfig", "--globalconfig", "--cache", "--logs-dir"}
    ),
}
_UNPARSEABLE_COMMAND_PATH = "<unparseable-command>"

_GIT_CONFIG_VALUE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("--file", "--fil"),
    ("--blob", "--blo"),
    ("--type", "--t"),
    ("--value", "--val"),
    ("--default", "--def"),
    ("--comment", "--co"),
)

_TAR_OLD_STYLE_OPTION_STARTS: frozenset[str] = frozenset("acdfrtuvx")

# Dangerous tar flags that execute arbitrary commands, delete files, or rewrite
# extracted paths outside the worktree.
# #140/#310: these options invoke shell commands, remove files, preserve
# leading absolute paths, or apply filename transformations during extraction.
_FORBIDDEN_TAR_FLAGS: frozenset[str] = frozenset(
    {
        # Use the shortest unambiguous GNU long-option prefixes so getopt
        # abbreviations cannot select an execution hook.
        "--to-c",
        "--checkpoint-a",
        "--use",
        "--info",
        "--new-v",
        "--rmt",
        "--rsh",
        "--remove",
        "--absolute-names",
        "--transform",
        "--xform",
    }
)

# Dangerous find predicates and actions. #135/#310: -delete silently removes
# files; -exec/-ok run arbitrary commands; -fprintf writes arbitrary content to
# a file chosen by the command.
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
        "-fprintf",
    }
)


def _normalize_path(path: str) -> str:
    """Return a normalized path for prefix comparison."""
    return normalize_path(path)


def _is_inside(path: str, forbidden: str) -> bool:
    """Return True if *path* is exactly *forbidden* or lives underneath it."""
    try:
        normalized_path = _normalize_path(path)
        normalized_forbidden = _normalize_path(forbidden)
    except ValueError:
        # Treat un-normalizable paths as forbidden so a bad entry cannot silently
        # bypass the check.
        return True
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
        # Keep command-parser failures fail closed for verification preflight,
        # even when no explicit forbidden path was configured.
        if touched == _UNPARSEABLE_COMMAND_PATH:
            conflicts.add(touched)
            continue
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
        # Callers use this result during verification preflight as well as
        # policy evaluation, so an uninspectable command must not disappear.
        return {_UNPARSEABLE_COMMAND_PATH}
    return _extract_argv_paths(argv)


def _is_path_argument_option(option: str, command: str = "") -> bool:
    """Return True for path options, including long-option abbreviations."""
    lowered = option.lower()
    command_options = _COMMAND_PATH_ARGUMENT_OPTIONS.get(command, frozenset())
    if lowered in {value.lower() for value in command_options}:
        return True
    if lowered.startswith("--") and any(
        canonical.lower().startswith(lowered)
        for canonical in command_options
        if canonical.startswith("--")
    ):
        return True
    if lowered in {value.lower() for value in _PATH_ARGUMENT_OPTIONS}:
        return True
    return lowered.startswith("--") and any(
        canonical.lower().startswith(lowered)
        for canonical in _PATH_ARGUMENT_OPTIONS
        if canonical.startswith("--")
    )


def _extract_argv_paths(argv: list[str]) -> set[str]:
    """Return path-like values from an already parsed argv."""
    paths: set[str] = set()
    base_command = _base_command(argv[0])
    for index, token in enumerate(argv[1:], start=1):
        lowered = token.lower()
        option = lowered.split("=", 1)[0]
        if (
            _is_path_argument_option(option, base_command)
            and index + 1 < len(argv)
            and "=" not in token
        ):
            paths.add(argv[index + 1])
            continue
        if token.startswith("--") and "=" in token:
            option, candidate = token.split("=", 1)
            if _is_path_argument_option(option, base_command) and candidate:
                paths.add(candidate)
                continue
        if token.startswith("-C") and len(token) > 2:
            paths.add(token[2:].lstrip("="))
            continue
        if token.startswith("-I") and len(token) > 2:
            paths.add(token[2:].lstrip("="))
            continue
        if token.startswith("-f") and len(token) > 2:
            paths.add(token[2:].lstrip("="))
            continue
        if token.startswith("-t") and len(token) > 2:
            paths.add(token[2:].lstrip("="))
            continue
        if (
            base_command in {"git", "go"}
            and token.lower().startswith("-o")
            and len(token) > 2
        ):
            paths.add(token[2:].lstrip("="))
            continue
        if (
            _base_command(argv[0]) == "tar"
            and token.startswith("-")
            and not token.startswith("--")
        ):
            short_options = token[1:]
            file_option_index = short_options.lower().find("f")
            if file_option_index >= 0:
                candidate = short_options[file_option_index + 1 :].lstrip("=")
                if candidate:
                    paths.add(candidate)
                elif index + 1 < len(argv):
                    paths.add(argv[index + 1])
                continue
        if (
            index == 1
            and base_command == "tar"
            and token
            and not token.startswith("-")
            and token[0].lower() in _TAR_OLD_STYLE_OPTION_STARTS
        ):
            file_option_index = token.lower().find("f")
            if file_option_index >= 0:
                candidate = token[file_option_index + 1 :].lstrip("=")
                if candidate:
                    paths.add(candidate)
                elif index + 1 < len(argv):
                    paths.add(argv[index + 1])
                continue
        if token.startswith("-"):
            # Some flags carry an inline path: -I/path, --file=/path, -I=path.
            for sep in ("=", ""):
                for flag_prefix in (
                    "-I",
                    "-T",
                    "--include",
                    "--exclude",
                    "--file",
                ):
                    prefix = flag_prefix + sep
                    if lowered.startswith(prefix.lower()):
                        candidate = token[len(prefix) :]
                        if candidate:
                            paths.add(candidate)
                            break
            continue
        # A positional operand can be a bare relative filename, so do not
        # discard it just because it has no slash.
        paths.add(token)
    child_index = _uv_run_child_index(argv)
    if child_index is not None:
        paths.update(_extract_argv_paths(argv[child_index:]))
    if base_command == "sed":
        for token in argv[1:]:
            _, sed_paths = _sed_file_io_paths(token)
            paths.update(sed_paths)
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


def _git_subcommand_index(argv: list[str]) -> int | None:
    """Return the actual git subcommand position after global options."""
    if not argv or _base_command(argv[0]) != "git":
        return None
    options_with_values = {
        "-C",
        "-c",
        "--config",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in options_with_values:
            index += 2
            continue
        if any(
            token.startswith(option + "=")
            for option in options_with_values
            if option.startswith("--")
        ):
            index += 1
            continue
        if token.startswith(("-C", "--config-env=")) and len(token) > 2:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return None


def _is_git_control_file_path(path: str) -> bool:
    """Return True for paths inside a repository's git control directory."""
    if path in {".", "./"}:
        return False
    try:
        normalized = _normalize_path(path)
    except ValueError:
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(part == ".git" for part in parts)


def _is_allowed_argv0(argv: list[str]) -> bool:
    """Return True if argv[0] is in the explicit allowlist."""
    return argv[0].lower() in _ALLOWED_VERIFICATION_COMMANDS


def _is_forbidden_wrapper(argv: list[str]) -> bool:
    """Return True if argv contains an opaque wrapper/interpreter payload."""
    if _base_command(argv[0]) in _FORBIDDEN_WRAPPER_COMMANDS:
        return True
    if _base_command(argv[0]) != "uv":
        return False
    try:
        run_index = argv.index("run", 1)
    except ValueError:
        return False
    return any(
        _base_command(token) in _FORBIDDEN_WRAPPER_COMMANDS
        for token in argv[run_index + 1 :]
    )


def _uv_run_child_index(argv: list[str]) -> int | None:
    """Return the direct child index for the restricted ``uv run`` form."""
    if _base_command(argv[0]) != "uv":
        return None
    try:
        run_index = argv.index("run", 1)
    except ValueError:
        return None
    child_index = run_index + 1
    if child_index < len(argv) and argv[child_index] == "--":
        child_index += 1
    if child_index >= len(argv) or argv[child_index].startswith("-"):
        return None
    return child_index


def _has_unsafe_uv_run(argv: list[str]) -> bool:
    """Return True if uv would execute an unallowlisted child command."""
    if _base_command(argv[0]) != "uv" or "run" not in argv[1:]:
        return False
    child_index = _uv_run_child_index(argv)
    return (
        child_index is None
        or _base_command(argv[child_index]) == "uv"
        or _base_command(argv[child_index]) not in _UV_RUN_CHILD_COMMANDS
    )


def _is_privilege_escalation(argv: list[str]) -> bool:
    """Return True if any token is a known privilege-escalation command."""
    return any(_base_command(token) in _FORBIDDEN_PRIVILEGE_COMMANDS for token in argv)


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


def _git_config_option_takes_value(token: str) -> bool:
    """Return True for git-config options with separate or attached values."""
    option = token.lower().split("=", 1)[0]
    if option in {"-f", "-t"}:
        return True
    return any(
        len(option) >= len(prefix) and canonical.startswith(option)
        for canonical, prefix in _GIT_CONFIG_VALUE_OPTIONS
    )


def _is_git_config_edit_option(token: str) -> bool:
    """Return True for git-config options that launch the configured editor."""
    option = token.lower().split("=", 1)[0]
    return option == "-e" or (
        option.startswith("--")
        and len(option) >= len("--e")
        and "--edit".startswith(option)
    )


def _git_long_option_matches(option: str, canonical: str) -> bool:
    """Return True when a Git long option is an unambiguous prefix."""
    return (
        option.startswith("--")
        and len(option) >= len("--x")
        and canonical.startswith(option)
    )


def _has_git_dangerous_config(argv: list[str]) -> bool:
    """Return True if git sets a dangerous config key via -c or git config."""
    if _base_command(argv[0]) != "git":
        return False

    def is_dangerous(key: str, value: str | None = None) -> bool:
        normalized_key = key.lower().strip()
        return normalized_key in _FORBIDDEN_GIT_CONFIG_KEYS or (
            normalized_key.startswith("alias.")
            and (value is None or value.lstrip().startswith("!"))
        ) or normalized_key == "core.gitproxy" or (
            normalized_key.startswith("remote.")
            and normalized_key.endswith((".uploadpack", ".receivepack"))
        ) or normalized_key == "core.askpass" or (
            normalized_key.startswith("filter.")
            and normalized_key.endswith((".clean", ".smudge", ".process"))
        ) or (
            normalized_key.startswith("diff.")
            and normalized_key.endswith(".textconv")
        ) or normalized_key == "diff.external" or (
            normalized_key.startswith("merge.")
            and normalized_key.endswith(".driver")
        ) or normalized_key in {"gpg.program", "sequence.editor"} or (
            normalized_key.startswith("includeif.")
            and normalized_key.endswith(".path")
        ) or (
            normalized_key.startswith("difftool.")
            and normalized_key.endswith(".cmd")
        ) or (
            normalized_key.startswith("mergetool.")
            and normalized_key.endswith(".cmd")
        ) or (
            normalized_key.startswith("submodule.")
            and normalized_key.endswith(".update")
        ) or normalized_key in {
            "gpg.ssh.defaultkeycommand",
            "gpg.ssh.program",
            "core.alternaterefscommand",
        } or (
            normalized_key.startswith("credential.")
            and normalized_key.endswith(".helper")
        ) or (
            normalized_key.startswith("diff.")
            and normalized_key.endswith(".command")
        )

    i = 1
    while i < len(argv):
        token = argv[i]
        if token in ("-c", "--config"):
            if i + 1 >= len(argv):
                return False
            key, separator, value = argv[i + 1].partition("=")
            if is_dangerous(key, value if separator else None):
                return True
            i += 2
            continue
        if token.lower().startswith("--config="):
            key, separator, value = token.split("=", 1)[1].partition("=")
            if is_dangerous(key, value if separator else None):
                return True
            i += 1
            continue
        if token.lower().startswith("-c"):
            config = token[2:]
            key, separator, value = config.partition("=")
            if is_dangerous(key, value if separator else None):
                return True
            i += 1
            continue
        if token.lower().startswith("--config-env="):
            config = token.split("=", 1)[1]
            key, _, _ = config.partition("=")
            if is_dangerous(key):
                return True
            i += 1
            continue
        if token.lower() == "--config-env":
            if i + 1 >= len(argv):
                return False
            key, _, _ = argv[i + 1].partition("=")
            if is_dangerous(key):
                return True
            i += 2
            continue
        if token == "config" and i == _git_subcommand_index(argv):
            # #309: persistent ``git config [<options>] <key> <value>`` sets the
            # key just like a ``git -c`` override. Skip option tokens and any
            # argument-taking options (--file/-f, --blob, --type/-t, --value,
            # --default, --comment) before looking at the key argument.
            j = i + 1
            while j < len(argv):
                opt = argv[j]
                if opt == "set":
                    j += 1
                    continue
                if _is_git_config_edit_option(opt):
                    return True
                if _git_config_option_takes_value(opt):
                    j += 1 if "=" in opt else 2
                    continue
                if opt.startswith("-f"):
                    # -f <path>, -f<path>, or -f=<path> all consume the argument.
                    j += 1 if opt != "-f" else 2
                    continue
                if opt.startswith("-"):
                    j += 1
                    continue
                break
            if j < len(argv):
                key, separator, value = argv[j].partition("=")
                if not separator and j + 1 < len(argv):
                    value = argv[j + 1]
                if is_dangerous(key, value if separator or value else None):
                    return True
            # No further interesting subcommands after ``git config``.
            return False
        i += 1
    return False


def _has_git_transport_execution(argv: list[str]) -> bool:
    """Return True for git options or subcommands that execute a supplied command."""
    if not argv or _base_command(argv[0]) != "git":
        return False
    subcommand_index = _git_subcommand_index(argv)
    subcommand = (
        argv[subcommand_index].lower()
        if subcommand_index is not None
        else ""
    )
    for token in argv[1:]:
        lowered = token.lower()
        option = lowered.split("=", 1)[0]
        if subcommand in {"difftool", "mergetool", "filter-branch"}:
            return True
        if subcommand == "submodule" and lowered == "foreach":
            return True
        if subcommand == "rebase" and (
            option == "-x" or option.startswith("-x")
        ):
            return True
        if option == "--exec-path":
            return True
        if subcommand == "bisect" and lowered == "run":
            return True
        if subcommand == "clone" and (
            lowered == "-u" or (lowered.startswith("-u") and len(lowered) > 2)
        ):
            return True
        if subcommand == "clone" and _git_long_option_matches(option, "--template"):
            return True
        if subcommand == "apply" and _git_long_option_matches(
            option, "--unsafe-paths"
        ):
            return True
        if _git_long_option_matches(option, "--upload-pack") or lowered.startswith(
            "ext::"
        ):
            return True
        if _git_long_option_matches(option, "--receive-pack"):
            return True
        if _git_long_option_matches(option, "--exec"):
            return True
    return False


def _has_git_force_push(argv: list[str]) -> bool:
    """Return True when ``git push`` can force-update or delete refs."""
    if not argv or _base_command(argv[0]) != "git":
        return False
    subcommand_index = _git_subcommand_index(argv)
    if subcommand_index is None or argv[subcommand_index].lower() != "push":
        return False
    for token in argv[subcommand_index + 1 :]:
        lowered = token.lower().split("=", 1)[0]
        if lowered in {"--force", "--force-with-lease", "-f"}:
            return True
        if _git_long_option_matches(lowered, "--force") or _git_long_option_matches(
            lowered, "--force-with-lease"
        ) or _git_long_option_matches(lowered, "--mirror"):
            return True
        if _git_long_option_matches(lowered, "--delete"):
            return True
        if token.startswith(("+", ":")) and len(token) > 1:
            return True
        if token.startswith("-") and not token.startswith("--") and any(
            flag in lowered for flag in ("f", "d")
        ):
            return True
    return False


def _has_zip_command_execution(argv: list[str]) -> bool:
    """Return True for zip's command-valued archive test option."""
    if not argv or _base_command(argv[0]) != "zip":
        return False
    for token in argv[1:]:
        lowered = token.lower()
        if lowered.startswith("--"):
            option = lowered.split("=", 1)[0]
            if option == "--test-command" or (
                option.startswith("--test-c")
                and "--test-command".startswith(option)
            ):
                return True
        elif (
            lowered.startswith("-")
            and not lowered.startswith("--")
            and "tt" in lowered[1:]
        ):
            # zip permits bundled short options, so -qTT and -qTT<command>
            # carry the same command-valued option as -TT.
            return True
    return False


def _has_git_control_file_target(argv: list[str]) -> bool:
    """Return True when an allowlisted command targets the git control directory."""
    if not argv:
        return False
    base_command = _base_command(argv[0])
    if base_command != "git" and base_command not in _GIT_CONTROL_FILE_WRITERS:
        return False
    return any(
        _is_git_control_file_path(path) for path in _extract_argv_paths(argv)
    )


def _has_tar_dangerous_flag(argv: list[str]) -> bool:
    """Return True if tar uses a destructive or path-rewriting flag."""
    if _base_command(argv[0]) != "tar":
        return False
    for index, token in enumerate(argv[1:], start=1):
        for flag in _FORBIDDEN_TAR_FLAGS:
            if token.lower().split("=", 1)[0].startswith(flag):
                return True
        # GNU tar's short aliases for --info-script, --use-compress-program,
        # and --absolute-names are -F, -I, and -P, including attached values.
        if token.startswith("-") and not token.startswith("--") and any(
            option in token[1:] for option in ("F", "I", "P")
        ):
            return True
        if (
            index == 1
            and token
            and not token.startswith("-")
            and token[0].lower() in _TAR_OLD_STYLE_OPTION_STARTS
            and any(option in token for option in ("F", "I", "P"))
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
    for i, token in enumerate(argv[1:], start=1):
        if token != "clean":
            continue
        for flag in argv[i + 1 :]:
            if flag in {"-f", "--force", "-x", "-d"}:
                return True
            if (
                flag.startswith("-")
                and len(flag) > 1
                and any(ch in flag.lower() for ch in "fxd")
            ):
                return True
    return False


def _sed_skip_address(script: str, index: int) -> int:
    """Return the index after one sed address, if one starts at *index*."""
    if index >= len(script):
        return index
    if script[index].isdigit():
        while index < len(script) and script[index].isdigit():
            index += 1
        if index < len(script) and script[index] == "~":
            index += 1
            while index < len(script) and script[index].isdigit():
                index += 1
        return index
    if script[index] == "$":
        return index + 1
    if script[index] not in ("/", "\\"):
        return index

    delimiter = script[index]
    index += 1
    if delimiter == "\\":
        if index >= len(script):
            return index
        delimiter = script[index]
        index += 1
    escaped = False
    while index < len(script):
        current = script[index]
        if escaped:
            escaped = False
        elif current == "\\":
            escaped = True
        elif current == delimiter:
            return index + 1
        index += 1
    return index


def _sed_file_io_paths(script: str) -> tuple[bool, set[str]]:
    """Return whether a sed script reads/writes a file and any file paths."""
    index = _sed_skip_address(script, 0)
    if index < len(script) and script[index] == ",":
        index = _sed_skip_address(script, index + 1)
    while index < len(script) and script[index].isspace():
        index += 1
    if index < len(script) and script[index] == "!":
        index += 1
        while index < len(script) and script[index].isspace():
            index += 1

    paths: set[str] = set()
    if index < len(script) and script[index] in "rRwW":
        path = script[index + 1 :].lstrip().split(None, 1)
        if path:
            paths.add(path[0])
        return True, paths

    if index >= len(script) or script[index] != "s" or index + 1 >= len(script):
        return False, paths

    delimiter = script[index + 1]
    delimiter_positions: list[int] = []
    escaped = False
    for position in range(index + 2, len(script)):
        current = script[position]
        if escaped:
            escaped = False
        elif current == "\\":
            escaped = True
        elif current == delimiter:
            delimiter_positions.append(position)
            if len(delimiter_positions) == 2:
                flags = script[position + 1 :]
                write_index = flags.lower().find("w")
                if write_index < 0:
                    return False, paths
                path = flags[write_index + 1 :].lstrip().split(None, 1)
                if path:
                    paths.add(path[0])
                return True, paths
    return False, paths


def _has_sed_dangerous_flag(argv: list[str]) -> bool:
    """Return True if sed uses the GNU `e` command or `s///e` flag.

    GNU sed's `e` address-command (`<addr>e <shell-command>`) and the
    `s///e` substitution flag both execute arbitrary shell commands. They
    are rejected regardless of quoting because they are unconditional RCE.
    """
    if _base_command(argv[0]) != "sed":
        return False
    script_tokens: list[str] = []
    explicit_script = False
    bare_script_seen = False
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in ("-i", "--in-place", "--i", "--in", "--inp") or (
            token.startswith("--in-place=") or token.startswith("--in-p")
        ):
            return True
        if token.startswith("-i") and not token.startswith("--"):
            return True
        if token in ("-e", "--expression", "-f", "--file"):
            if token in ("-f", "--file"):
                return True
            explicit_script = True
            i += 1
            if i < len(argv):
                script_tokens.append(argv[i])
        elif token.startswith("--file=") or token.startswith("--fi") or (
            token.startswith("-f") and token != "-f"
        ):
            return True
        elif token.startswith("--expression=") or token.startswith("--expr="):
            explicit_script = True
            script_tokens.append(token.split("=", 1)[1])
        elif token.startswith("--e"):
            explicit_script = True
            if "=" in token:
                script_tokens.append(token.split("=", 1)[1])
            else:
                i += 1
                if i < len(argv):
                    script_tokens.append(argv[i])
        elif token.startswith("-e"):
            explicit_script = True
            script_tokens.append(token[2:])
        elif token.startswith("-") and not token.startswith("--"):
            short_options = token[1:]
            if "f" in short_options:
                return True
            if "e" in short_options:
                explicit_script = True
                expression = short_options[short_options.index("e") + 1 :]
                if expression:
                    script_tokens.append(expression)
                else:
                    i += 1
                    if i < len(argv):
                        script_tokens.append(argv[i])
        elif (
            not token.startswith("-")
            and not explicit_script
            and not bare_script_seen
        ):
            script_tokens.append(token)
            bare_script_seen = True
        i += 1
    for script in script_tokens:
        # Detect s///e, s/.../.../e, and s@...@...@e etc.
        # The flag 'e' must appear after the final delimiter; for safety we
        # reject any substitution followed by 'e' as a trailing flag.
        for substitution_index, character in enumerate(script):
            if character != "s" or substitution_index + 1 >= len(script):
                continue
            delim = script[substitution_index + 1]
            if delim.isspace():
                continue
            delimiter_positions: list[int] = []
            escaped = False
            for position in range(substitution_index + 2, len(script)):
                current = script[position]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == delim:
                    delimiter_positions.append(position)
                    if len(delimiter_positions) == 2:
                        flags = script[position + 1 :]
                        if "e" in flags.lower():
                            return True
                        break
        # sed r/R reads a file and w/W writes one without invoking a shell.
        file_io, _ = _sed_file_io_paths(script)
        if file_io:
            return True
        # Detect the bare 'e' command, e.g. '1e id', '$e touch /tmp/x', or
        # '/pattern/e touch /tmp/x'. A regex address can contain any text, so
        # the command boundary is the reliable part to match.
        if re.search(
            r"(^|[;\n])\s*[^;\n]*?e(?=\s|$|;)",
            script,
            re.IGNORECASE,
        ):
            return True
    return False


def _has_tar_write_operation(argv: list[str]) -> bool:
    """Return True for tar operations that write extracted archive members."""
    if _base_command(argv[0]) != "tar":
        return False
    for index, token in enumerate(argv[1:], start=1):
        lowered = token.lower()
        if lowered == "--get" or (
            len(lowered) >= len("--ge") and "--get".startswith(lowered)
        ):
            return True
        if len(lowered) >= len("--ext") and "--extract".startswith(lowered):
            return True
        if (
            token.startswith("-")
            and not token.startswith("--")
            and "x" in token.lower()
        ):
            return True
        if (
            index == 1
            and token
            and not token.startswith("-")
            and token[0].lower() in _TAR_OLD_STYLE_OPTION_STARTS
            and "x" in token.lower()
        ):
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
            lowered = token.lower()
            if "r" in lowered:
                recursive = True
            if "f" in lowered:
                force = True
    return recursive and force


def _is_destructive_command(argv: list[str]) -> bool:
    """Return True if *argv* looks like a destructive file operation."""
    return bool(
        any(_base_command(token) in _FORBIDDEN_DESTRUCTIVE_COMMANDS for token in argv)
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
        or _has_git_transport_execution(argv)
        or _has_zip_command_execution(argv)
        or _has_git_control_file_target(argv)
        or _has_tar_dangerous_flag(argv)
        or _has_tar_write_operation(argv)
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
            f"Command uses a wrapper/interpreter that can hide payloads: {command!r}"
        )
    elif not _is_allowed_argv0(argv):
        local_violations.append(
            f"Command argv[0] is not in the verification allowlist: {command!r}"
        )

    if _has_unsafe_uv_run(argv):
        local_violations.append(
            "Command uses uv run with an unallowlisted wrapper/interpreter child: "
            f"{command!r}"
        )

    if _is_privilege_escalation(argv):
        local_violations.append(f"Command contains privilege escalation: {command!r}")

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
            f"Command is destructive but profile denies destructive_shell: {command!r}"
        )

    if _has_git_force_push(argv) and profile.git.force_push == "deny":
        violations.append(f"Force push is denied by project profile: {command!r}")

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
            violations.extend(_validate_command_against_profile(command, profile))

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
        try:
            conflicts = _forbidden_path_conflicts(touched_paths, forbidden_paths)
        except ValueError:
            # A forbidden path entry cannot be normalized; fail closed.
            conflicts = set()
            violations.append(
                "Forbidden path list contains an un-normalizable entry"
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
