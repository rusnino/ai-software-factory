package governance.approve

import future.keywords.if
import future.keywords.in

# ---------------------------------------------------------------------------
# Rego policy that mirrors the embedded PolicyEngine checks for Phase 2.
# This is a parity policy: enabling OPA should not be a downgrade from the
# embedded engine.  It validates harness allowlist, dangerous wrappers,
# shell metacharacters, destructive flags, container-escape flags, and
# project-profile security fields.
#
# The input document is the data-minimized shape produced by
# PolicyEngineBackend._minimal_opa_input, NOT the full contract/profile.
# ---------------------------------------------------------------------------

default allow := false

allow if {
    count(violations) == 0
}

violations := [v | v := _violation[_]]

# Aggregate all violation sources.
_violation contains _harness_violation[_]
_violation contains _wrapper_violation[_]
_violation contains _metachar_violation[_]
_violation contains _destructive_violation[_]
_violation contains _container_escape_violation[_]
_violation contains _profile_security_violation[_]
_violation contains _git_config_violation[_]
_violation contains _network_tool_violation[_]
_violation contains _approval_chain_violation[_]
_violation contains _approval_type_violation[_]
_violation contains _completeness_violation[_]
_violation contains _command_allowlist_violation[_]
_violation contains _privilege_violation[_]
_violation contains _control_character_violation[_]
_violation contains _command_execution_violation[_]
_violation contains _docker_socket_violation[_]
_violation contains _harness_role_violation[_]
_violation contains _resource_cap_violation[_]
_violation contains _command_parse_violation[_]
_violation contains _path_conflict_violation[_]

# ---------------------------------------------------------------------------
# Input accessors (defensive defaults for missing keys)
# ---------------------------------------------------------------------------

_execution := object.get(input, "execution", {})
_security := object.get(input, "security", {})
_git := object.get(input, "git", {})
_approval := object.get(input, "approval", {})
_profile_execution := object.get(input, "profile_execution", {})
_harness_roles := object.get(input, "harness_roles", {})

# ---------------------------------------------------------------------------
# Harness allowlist
# ---------------------------------------------------------------------------

_allowed_harnesses := array.concat(
    object.get(input, "allowed_harnesses", []),
    []
)

_harness_violation contains msg if {
    harness := object.get(_execution, "harness", null)
    harness != null
    harness != ""
    not harness in _allowed_harnesses
    msg := sprintf("Harness '%s' is not in project profile allowlist", [harness])
}

# ---------------------------------------------------------------------------
# Wrapper / interpreter commands
# ---------------------------------------------------------------------------

_forbidden_wrappers := {"bash", "sh", "dash", "zsh", "python", "python3", "node", "ruby", "perl", "php", "lua", "env", "xargs", "nice", "nohup", "ssh", "timeout", "command", "exec", "eval", "busybox", "install"}

_wrapper_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) in _forbidden_wrappers
    msg := sprintf("Forbidden wrapper/interpreter command: %s", [_base_command(argv[0])])
}

_allowed_commands := {
    "brew", "cargo", "cmake", "composer", "conan", "dotnet", "gem",
    "gradle", "make", "meson", "mix", "mvn", "npm", "npx", "nuget",
    "pip", "pip3", "pnpm", "poetry", "raco", "rake", "sbt", "stack",
    "uv", "yarn", "git", "hg", "svn", "pytest", "tox", "nox", "jest",
    "mocha", "go", "gotestsum", "prove", "rspec", "unittest", "vitest",
    "bandit", "black", "flake8", "mypy", "pylint", "pyright", "ruff",
    "cat", "cp", "cut", "date", "diff", "echo", "find", "grep", "head",
    "id", "ls", "mkdir", "mv", "pwd", "rm", "sed", "sort", "tail", "tar",
    "tee", "test", "touch", "tr", "uniq", "unzip", "wc", "which", "whoami",
    "zip",
}

_command_allowlist_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    not _base_command(argv[0]) in _allowed_commands
    msg := sprintf("Command argv[0] is not in the verification allowlist: %s", [cmd])
}

# ---------------------------------------------------------------------------
# Shell metacharacters and forbidden network tools
# ---------------------------------------------------------------------------

_forbidden_metachars := {";", "&", "|", ">", "<", "`", "$", "(", ")", "{", "}", "*", "?", "[", "]", "~", "#", "\n", "\r"}

_metachar_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    some char in _forbidden_metachars
    contains(arg, char)
    msg := sprintf("Command contains forbidden shell metacharacter: %q", [char])
}

_control_character_violation contains msg if {
    some cmd in _commands
    contains(cmd, "\u0000")
    msg := sprintf("Command contains forbidden control character: %q", ["NUL"])
}

_control_character_violation contains msg if {
    some cmd in _commands
    contains(cmd, "\n")
    msg := "Command contains forbidden control character: newline"
}

_control_character_violation contains msg if {
    some cmd in _commands
    contains(cmd, "\r")
    msg := "Command contains forbidden control character: carriage return"
}

_privilege_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    _base_command(arg) in {"sudo", "su", "doas"}
    msg := sprintf("Command contains privilege escalation: %s", [cmd])
}

_forbidden_network_tools := {"curl", "wget"}

_network_tool_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) in _forbidden_network_tools
    msg := sprintf("Forbidden network fetch tool: %s", [_base_command(argv[0])])
}

# ---------------------------------------------------------------------------
# Contract completeness
# ---------------------------------------------------------------------------

_completeness_violation contains msg if {
    object.get(input, "has_objective", true) == false
    msg := "Task contract objective is empty"
}

_completeness_violation contains msg if {
    object.get(input, "has_acceptance", true) == false
    msg := "Task contract acceptance criteria are empty"
}

# ---------------------------------------------------------------------------
# Destructive flags and command-execution primitives
# ---------------------------------------------------------------------------

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "rm"
    _short_flag_present(argv, "r")
    _short_flag_present(argv, "f")
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "rm"
    some arg in argv
    startswith(arg, "-")
    contains(lower(arg), "r")
    contains(lower(arg), "f")
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "rm"
    some arg in argv
    arg in {"--recursive", "--force", "--no-preserve-root"}
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    startswith(_base_command(arg), "mkfs")
    msg := sprintf("Destructive filesystem command: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    regex.match(`(?i).*([^[:alnum:]]|^)([0-9]+|\$|%)?e[[:space:]].*`, cmd)
    msg := sprintf("Sed command-execution primitive: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    regex.match(`(?i).*(^|[[:space:]])s[^[:space:]]*e([[:space:]]|$).*`, cmd)
    msg := sprintf("Sed command-execution primitive: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "dd"
    some input_arg in argv
    startswith(lower(input_arg), "if=")
    some output_arg in argv
    startswith(lower(output_arg), "of=/")
    msg := sprintf("Dangerous dd target in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "find"
    some arg in argv
    lower(arg) in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint", "-fprint0"}
    msg := sprintf("Dangerous find action in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some arg in argv
    some flag in {"--to-command", "--remove-files", "--remove-file"}
    lower(arg) == flag
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some arg in argv
    some flag in {"--to-command", "--remove-files", "--remove-file"}
    startswith(lower(arg), sprintf("%s=", [flag]))
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    count(argv) > 1
    argv[1] == "clean"
    some index
    index >= 2
    arg := argv[index]
    _git_clean_dangerous_flag(arg)
    msg := sprintf("Dangerous git clean flags in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index
    arg := argv[index]
    index >= 1
    _git_config_key(argv, index) != ""
    lower(_git_config_key(argv, index)) in {
        "core.sshcommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "credential.helper",
        "include.path",
    }
    msg := sprintf("Dangerous git config override in command: %s", [cmd])
}

# ---------------------------------------------------------------------------
# Container escape flags
# ---------------------------------------------------------------------------

_container_escape_flags := {"--privileged", "--network=host", "--volume", "--mount", "--pid=host", "--ipc=host", "--uts=host", "--security-opt", "--cap-add"}

_container_escape_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    some flag in _container_escape_flags
    arg == flag
    msg := sprintf("Container escape flag in command: %s", [arg])
}

_container_escape_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    some flag in _container_escape_flags
    startswith(arg, sprintf("%s=", [flag]))
    msg := sprintf("Container escape flag in command: %s", [arg])
}

_docker_socket_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    contains(lower(arg), "docker.sock")
    object.get(_security, "docker_socket", "deny") == "deny"
    msg := sprintf("Command references docker socket: %s", [cmd])
}

_docker_socket_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    some arg in argv
    contains(lower(arg), "/var/run/docker.sock")
    object.get(_security, "docker_socket", "deny") == "deny"
    msg := sprintf("Command references docker socket: %s", [cmd])
}

# ---------------------------------------------------------------------------
# Registered harness roles and execution resource caps
# ---------------------------------------------------------------------------

_harness_role_violation contains msg if {
    count(_harness_roles) > 0
    harness := object.get(_execution, "harness", "")
    not harness in object.keys(_harness_roles)
    msg := sprintf("Harness '%s' is not registered", [harness])
}

_harness_role_violation contains msg if {
    count(_harness_roles) > 0
    harness := object.get(_execution, "harness", "")
    role := object.get(_execution, "role", "")
    roles := object.get(_harness_roles, harness, [])
    not role in roles
    msg := sprintf("Role '%s' is not allowed by harness '%s'", [role, harness])
}

_resource_cap_violation contains msg if {
    count(_profile_execution) > 0
    requested := object.get(_execution, "timeout_minutes", 0)
    cap := object.get(_profile_execution, "timeout_minutes", requested)
    requested > cap
    msg := sprintf("Task timeout_minutes (%d) exceeds project cap (%d)", [requested, cap])
}

_resource_cap_violation contains msg if {
    count(_profile_execution) > 0
    requested := object.get(_execution, "max_retries", 0)
    cap := object.get(_profile_execution, "max_retries", requested)
    requested > cap
    msg := sprintf("Task max_retries (%d) exceeds project cap (%d)", [requested, cap])
}

# ---------------------------------------------------------------------------
# Profile security fields
# ---------------------------------------------------------------------------

_profile_security_violation contains msg if {
    object.get(_execution, "uses_docker_socket", false) == true
    object.get(_security, "docker_socket", "deny") == "deny"
    msg := "Task uses docker socket but project profile does not allow it"
}

_profile_security_violation contains msg if {
    object.get(_execution, "destructive_shell", false) == true
    object.get(_security, "destructive_shell", "deny") == "deny"
    msg := "Task declares destructive shell but project profile does not allow it"
}

_profile_security_violation contains msg if {
    object.get(_execution, "network_access", "restricted") == "unrestricted"
    object.get(_security, "network", "restricted") == "restricted"
    msg := "Task declares network access but project profile does not allow it"
}

_profile_security_violation contains msg if {
    object.get(_execution, "spawn_subagents", false) == true
    object.get(_security, "spawn_subagents", "deny") == "deny"
    msg := "Task declares subagent spawning but project profile does not allow it"
}

# ---------------------------------------------------------------------------
# Forbidden paths
# ---------------------------------------------------------------------------

_path_conflict_violation contains msg if {
    some path in object.get(input, "forbidden_path_conflicts", [])
    msg := sprintf("Task touches forbidden path: %s", [path])
}

# ---------------------------------------------------------------------------
# Approval chain / self-approval
# ---------------------------------------------------------------------------

_approval_actor := object.get(_approval, "actor", "")

_approval_chain_violation contains msg if {
    input.proposed_by == _approval_actor
    _approval_actor != ""
    msg := "Self-approval is not allowed"
}

_approval_chain_violation contains msg if {
    startswith(_approval_actor, "agent:")
    msg := "Agent actors cannot grant approvals"
}

_approval_chain_violation contains msg if {
    startswith(_approval_actor, "system:")
    msg := "System actors cannot grant approvals"
}

# ---------------------------------------------------------------------------
# Approval type-driven checks (merge gate)
# ---------------------------------------------------------------------------

_approval_type_violation contains msg if {
    input.approval_type == "merge"
    object.get(_git, "merge_requires_human", true) == false
    msg := "Merge approval requires human merge gate in profile"
}

_approval_type_violation contains msg if {
    input.approval_type == "merge"
    object.get(_git, "force_push", "deny") == "deny"
    object.get(_execution, "force_push", false) == true
    msg := "Force push is denied by project profile"
}

_approval_type_violation contains msg if {
    input.approval_type == "merge"
    object.get(_git, "signed_commits", "optional") == "required"
    object.get(_execution, "signed_commits", false) == false
    msg := "Signed commits are required by project profile"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_commands := object.get(input, "commands", [])
_parsed_commands := object.get(input, "parsed_commands", [])

_git_clean_dangerous_flag(arg) if {
    arg in {"-f", "--force", "-x", "-d"}
}

_git_clean_dangerous_flag(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    some flag in {"f", "x", "d"}
    contains(arg, flag)
}

_git_config_key(argv, index) := key if {
    arg := argv[index]
    arg in {"-c", "--config"}
    next := argv[index + 1]
    parts := split(next, "=")
    key := parts[0]
}

_git_config_key(argv, index) := key if {
    arg := argv[index]
    startswith(lower(arg), "-c")
    not arg in {"-c", "--config"}
    config := substring(arg, 2, -1)
    parts := split(config, "=")
    key := parts[0]
}

_base_command(token) := base if {
    parts := split(lower(token), "/")
    base := parts[count(parts) - 1]
}

_command_argv(cmd) := argv if {
    some record in _parsed_commands
    object.get(record, "raw", "") == cmd
    argv := object.get(record, "argv", [])
} else := _shlex_split(cmd)

_command_parse_violation contains msg if {
    some record in _parsed_commands
    error := object.get(record, "error", "")
    error != ""
    msg := sprintf("Command cannot be parsed: %s", [error])
}

_command_parse_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) == 0
    msg := sprintf("Command is empty after parsing: %s", [cmd])
}

_short_flag_present(argv, flag) if {
    some arg in argv
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(arg, flag)
}

_shlex_split(cmd) := argv if {
    # Rego has no shlex.  This simple split is intentionally conservative:
    # it splits on whitespace and strips surrounding quotes.  It will err on
    # the side of missing a violation rather than rejecting a benign command.
    argv := [token |
        some raw in split(cmd, " ")
        trimmed := trim(trim(raw, "\""), "'")
        trimmed != ""
        token := trimmed
    ]
} else := []
