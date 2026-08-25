package governance.approve

import future.keywords.if
import future.keywords.in

# ---------------------------------------------------------------------------
# Rego policy that mirrors the embedded PolicyEngine checks for Phase 2.
# This is a parity policy: enabling OPA should not be a downgrade from the
# embedded engine.  It validates harness allowlist, dangerous wrappers,
# shell metacharacters, destructive flags, container-escape flags, and
# project-profile security fields.
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
_violation contains _forbidden_path_violation[_]
_violation contains _network_tool_violation[_]
_violation contains _approval_chain_violation[_]

# ---------------------------------------------------------------------------
# Harness allowlist
# ---------------------------------------------------------------------------

_allowed_harnesses := array.concat(
    object.get(input.profile, "execution", {"allowed_harnesses": []}).allowed_harnesses,
    []
)

_harness_violation contains msg if {
    harness := object.get(input.contract, "execution", {}).harness
    harness != null
    not harness in _allowed_harnesses
    msg := sprintf("Harness '%s' is not in project profile allowlist", [harness])
}

# ---------------------------------------------------------------------------
# Wrapper / interpreter commands
# ---------------------------------------------------------------------------

_forbidden_wrappers := {"bash", "sh", "dash", "zsh", "python", "python3", "node", "ruby", "perl", "php", "env", "xargs", "nice", "nohup", "ssh", "timeout", "script"}

_wrapper_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    count(argv) > 0
    argv[0] in _forbidden_wrappers
    msg := sprintf("Forbidden wrapper/interpreter command: %s", [argv[0]])
}

# ---------------------------------------------------------------------------
# Shell metacharacters and forbidden network tools
# ---------------------------------------------------------------------------

_forbidden_metachars := {";", "&", "|", ">", "<", "`", "$", "(", ")", "{", "}", "*", "?", "[", "]", "~", "#", "\n", "\r"}

_metachar_violation contains msg if {
    some cmd in _all_commands
    some char in _forbidden_metachars
    contains(cmd, char)
    msg := sprintf("Command contains forbidden shell metacharacter: %q", [char])
}

_forbidden_network_tools := {"curl", "wget"}

_network_tool_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    count(argv) > 0
    argv[0] in _forbidden_network_tools
    msg := sprintf("Forbidden network fetch tool: %s", [argv[0]])
}

# ---------------------------------------------------------------------------
# Destructive flags and command-execution primitives
# ---------------------------------------------------------------------------

_destructive_violation contains msg if {
    some cmd in _all_commands
    lower_cmd := lower(cmd)
    contains(lower_cmd, "rm")
    regex.match(`.*rm\\s+.*(-rf|-fr|--no-preserve-root).*`, lower_cmd)
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _all_commands
    lower_cmd := lower(cmd)
    contains(lower_cmd, "dd")
    contains(lower_cmd, "of=/dev")
    msg := sprintf("Dangerous dd target in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    argv[0] == "find"
    some arg in argv
    arg in {"-delete", "-exec", "-ok"}
    msg := sprintf("Dangerous find action in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    argv[0] == "tar"
    some arg in argv
    arg in {"--to-command", "--remove-files"}
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    argv[0] == "git"
    some arg in argv
    arg in {"clean", "-f", "-x", "-d"}
    contains(lower(cmd), "git clean")
    msg := sprintf("Dangerous git clean flags in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    argv[0] == "git"
    some arg in argv
    startswith(arg, "-c")
    dangerous_key := [k | k := ["core.sshcommand", "core.fsmonitor", "core.editor", "credential.helper", "user.signingkey"][_]]
    lower_arg := lower(arg)
    some key in dangerous_key
    contains(lower_arg, key)
    msg := sprintf("Dangerous git config override in command: %s", [cmd])
}

# ---------------------------------------------------------------------------
# Container escape flags
# ---------------------------------------------------------------------------

_container_escape_flags := {"--privileged", "--network=host", "--volume", "--mount", "--pid=host", "--ipc=host", "--uts=host", "--security-opt", "--cap-add"}

_container_escape_violation contains msg if {
    some cmd in _all_commands
    argv := _shlex_split(cmd)
    some arg in argv
    arg in _container_escape_flags
    msg := sprintf("Container escape flag in command: %s", [arg])
}

# ---------------------------------------------------------------------------
# Profile security fields
# ---------------------------------------------------------------------------

_profile_security := object.get(input.profile, "security", {})

_profile_security_violation contains msg if {
    input.contract.execution.uses_docker_socket == true
    not _profile_security.docker_socket
    msg := "Task uses docker socket but project profile does not allow it"
}

_profile_security_violation contains msg if {
    input.contract.execution.destructive_shell == true
    not _profile_security.destructive_shell
    msg := "Task declares destructive shell but project profile does not allow it"
}

_profile_security_violation contains msg if {
    input.contract.execution.network_access == true
    not _profile_security.network
    msg := "Task declares network access but project profile does not allow it"
}

# ---------------------------------------------------------------------------
# Forbidden paths
# ---------------------------------------------------------------------------

_forbidden_paths := ["/etc/passwd", "/etc/shadow", "/etc/hosts", "id_rsa", "id_ed25519", ".aws/", ".ssh/", "docker.sock"]

_forbidden_path_violation contains msg if {
    some cmd in _all_commands
    some path in _forbidden_paths
    contains(cmd, path)
    msg := sprintf("Command references forbidden path: %s", [path])
}

# ---------------------------------------------------------------------------
# Approval chain / self-approval
# ---------------------------------------------------------------------------

_approval_chain_violation contains msg if {
    input.contract.proposed_by == input.approval.actor
    msg := "Self-approval is not allowed"
}

_approval_chain_violation contains msg if {
    startswith(input.approval.actor, "agent:")
    msg := "Agent actors cannot grant approvals"
}

_approval_chain_violation contains msg if {
    startswith(input.approval.actor, "system:")
    msg := "System actors cannot grant approvals"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_all_commands := [cmd |
    some source in ["verification", "completion"]
    cmd := _commands_from(source)[_]
]

_commands_from("verification") := cmds if {
    cmds := [c.command | some c in object.get(input.contract, "verification", {}).required]
} else := []

_commands_from("completion") := cmds if {
    cmds := [c.command | some c in object.get(input.contract, "completion", {}).checks]
} else := []

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
