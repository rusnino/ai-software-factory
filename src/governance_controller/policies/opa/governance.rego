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
_violation contains _forbidden_path_violation[_]
_violation contains _network_tool_violation[_]
_violation contains _approval_chain_violation[_]
_violation contains _approval_type_violation[_]

# ---------------------------------------------------------------------------
# Input accessors (defensive defaults for missing keys)
# ---------------------------------------------------------------------------

_execution := object.get(input, "execution", {})
_security := object.get(input, "security", {})
_git := object.get(input, "git", {})
_approval := object.get(input, "approval", {})

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

_forbidden_wrappers := {"bash", "sh", "dash", "zsh", "python", "python3", "node", "ruby", "perl", "php", "env", "xargs", "nice", "nohup", "ssh", "timeout", "script"}

_wrapper_violation contains msg if {
    some cmd in _commands
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
    some cmd in _commands
    some char in _forbidden_metachars
    contains(cmd, char)
    msg := sprintf("Command contains forbidden shell metacharacter: %q", [char])
}

_forbidden_network_tools := {"curl", "wget"}

_network_tool_violation contains msg if {
    some cmd in _commands
    argv := _shlex_split(cmd)
    count(argv) > 0
    argv[0] in _forbidden_network_tools
    msg := sprintf("Forbidden network fetch tool: %s", [argv[0]])
}

# ---------------------------------------------------------------------------
# Destructive flags and command-execution primitives
# ---------------------------------------------------------------------------

_destructive_violation contains msg if {
    some cmd in _commands
    lower_cmd := lower(cmd)
    contains(lower_cmd, "rm")
    regex.match(`.*rm\s+.*(-rf|-fr|--no-preserve-root).*`, lower_cmd)
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    lower_cmd := lower(cmd)
    contains(lower_cmd, "dd")
    contains(lower_cmd, "of=/dev")
    msg := sprintf("Dangerous dd target in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _shlex_split(cmd)
    argv[0] == "find"
    some arg in argv
    arg in {"-delete", "-exec", "-ok"}
    msg := sprintf("Dangerous find action in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _shlex_split(cmd)
    argv[0] == "tar"
    some arg in argv
    arg in {"--to-command", "--remove-files"}
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _shlex_split(cmd)
    argv[0] == "git"
    some arg in argv
    arg in {"clean", "-f", "-x", "-d"}
    contains(lower(cmd), "git clean")
    msg := sprintf("Dangerous git clean flags in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
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
    some cmd in _commands
    argv := _shlex_split(cmd)
    some arg in argv
    arg in _container_escape_flags
    msg := sprintf("Container escape flag in command: %s", [arg])
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

_all_forbidden_paths := array.concat(
    object.get(input, "forbidden_paths", []),
    object.get(_security, "forbidden_paths", [])
)

_forbidden_path_violation contains msg if {
    some cmd in _commands
    some path in _all_forbidden_paths
    contains(cmd, path)
    msg := sprintf("Command references forbidden path: %s", [path])
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
