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
_violation contains _uv_run_violation[_]
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
_violation contains _git_transport_violation[_]
_violation contains _git_command_execution_violation[_]
_violation contains _git_force_push_violation[_]
_violation contains _docker_socket_violation[_]
_violation contains _harness_role_violation[_]
_violation contains _resource_cap_violation[_]
_violation contains _command_parse_violation[_]
_violation contains _path_conflict_violation[_]
_violation contains _control_file_violation[_]
_violation contains _input_shape_violation[_]

# ---------------------------------------------------------------------------
# Input accessors (defensive defaults for missing keys)
# ---------------------------------------------------------------------------

_execution := object.get(input, "execution", {})
_security := object.get(input, "security", {})
_git := object.get(input, "git", {})
_approval := object.get(input, "approval", {})
_profile_execution := object.get(input, "profile_execution", {})
_harness_roles := object.get(input, "harness_roles", {})

_required_string_input_fields := {
    "task_id",
    "project_id",
    "proposed_by",
    "approval_type",
}

_required_boolean_input_fields := {"has_objective", "has_acceptance"}

_required_array_input_fields := {
    "commands",
    "parsed_commands",
    "allowed_harnesses",
    "forbidden_path_conflicts",
    "forbidden_paths",
}

_required_object_input_fields := {
    "approval",
    "execution",
    "security",
    "git",
    "profile_execution",
    "harness_roles",
}

_input_shape_violation contains msg if {
    some key in _required_string_input_fields
    type_name(object.get(input, key, null)) != "string"
    msg := sprintf("OPA input field '%s' must be a string", [key])
}

_input_shape_violation contains msg if {
    some key in _required_boolean_input_fields
    type_name(object.get(input, key, null)) != "boolean"
    msg := sprintf("OPA input field '%s' must be a boolean", [key])
}

_input_shape_violation contains msg if {
    some key in _required_array_input_fields
    type_name(object.get(input, key, null)) != "array"
    msg := sprintf("OPA input field '%s' must be an array", [key])
}

_input_shape_violation contains msg if {
    some cmd in _commands
    not _has_parsed_command(cmd)
    msg := sprintf("OPA input parsed_commands is missing command: %s", [cmd])
}

_input_shape_violation contains msg if {
    some cmd in _commands
    some record in _parsed_commands
    object.get(record, "raw", "") == cmd
    argv := object.get(record, "argv", [])
    expected := _shlex_split(cmd)
    count(argv) > 0
    count(expected) > 0
    lower(argv[0]) != lower(expected[0])
    msg := sprintf("OPA parsed argv does not match command executable: %s", [cmd])
}

_input_shape_violation contains msg if {
    some cmd in _commands
    not contains(cmd, "'")
    not contains(cmd, "\"")
    not contains(cmd, "\\")
    some record in _parsed_commands
    object.get(record, "raw", "") == cmd
    argv := object.get(record, "argv", [])
    expected := _shlex_split(cmd)
    argv != expected
    msg := sprintf("OPA parsed argv does not match command tokens: %s", [cmd])
}

_input_shape_violation contains msg if {
    some key in _required_object_input_fields
    type_name(object.get(input, key, null)) != "object"
    msg := sprintf("OPA input field '%s' must be an object", [key])
}

_required_approval_fields := {"actor", "type"}
_required_execution_string_fields := {"harness", "role", "network_access"}
_required_execution_boolean_fields := {
    "uses_docker_socket",
    "destructive_shell",
    "spawn_subagents",
    "force_push",
    "signed_commits",
}
_required_execution_number_fields := {"timeout_minutes", "max_retries"}
_required_security_string_fields := {
    "docker_socket",
    "destructive_shell",
    "network",
    "spawn_subagents",
}
_required_git_string_fields := {"force_push", "signed_commits"}
_required_git_boolean_fields := {"merge_requires_human"}
_required_profile_execution_number_fields := {"timeout_minutes", "max_retries"}

_input_shape_violation contains msg if {
    type_name(_approval) == "object"
    some key in _required_approval_fields
    type_name(object.get(_approval, key, null)) != "string"
    msg := sprintf("OPA input approval field '%s' must be a string", [key])
}

_input_shape_violation contains msg if {
    type_name(_execution) == "object"
    some key in _required_execution_string_fields
    type_name(object.get(_execution, key, null)) != "string"
    msg := sprintf("OPA input execution field '%s' must be a string", [key])
}

_input_shape_violation contains msg if {
    type_name(_execution) == "object"
    some key in _required_execution_boolean_fields
    type_name(object.get(_execution, key, null)) != "boolean"
    msg := sprintf("OPA input execution field '%s' must be a boolean", [key])
}

_input_shape_violation contains msg if {
    type_name(_execution) == "object"
    some key in _required_execution_number_fields
    type_name(object.get(_execution, key, null)) != "number"
    msg := sprintf("OPA input execution field '%s' must be a number", [key])
}

_input_shape_violation contains msg if {
    type_name(_security) == "object"
    some key in _required_security_string_fields
    type_name(object.get(_security, key, null)) != "string"
    msg := sprintf("OPA input security field '%s' must be a string", [key])
}

_input_shape_violation contains msg if {
    type_name(_git) == "object"
    some key in _required_git_string_fields
    type_name(object.get(_git, key, null)) != "string"
    msg := sprintf("OPA input git field '%s' must be a string", [key])
}

_input_shape_violation contains msg if {
    type_name(_git) == "object"
    some key in _required_git_boolean_fields
    type_name(object.get(_git, key, null)) != "boolean"
    msg := sprintf("OPA input git field '%s' must be a boolean", [key])
}

_input_shape_violation contains msg if {
    type_name(_profile_execution) == "object"
    some key in _required_profile_execution_number_fields
    type_name(object.get(_profile_execution, key, null)) != "number"
    msg := sprintf("OPA input profile_execution field '%s' must be a number", [key])
}

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

_wrapper_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "uv"
    some run_index, arg in argv
    run_index > 0
    arg == "run"
    some child_index, child in argv
    child_index > run_index
    _base_command(child) in _forbidden_wrappers
    msg := sprintf("Nested wrapper/interpreter command: %s", [cmd])
}

_uv_run_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "uv"
    child_index := _uv_run_child_index(argv)
    child_index == -1
    msg := sprintf("uv run child is not in the verification allowlist: %s", [cmd])
}

_uv_run_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "uv"
    child_index := _uv_run_child_index(argv)
    child_index != -1
    not _base_command(argv[child_index]) in _uv_run_child_commands
    msg := sprintf("uv run child is not in the verification allowlist: %s", [cmd])
}

# unzip is intentionally omitted: archive extraction writes untrusted members.
_allowed_commands := {
    "brew", "cargo", "cmake", "composer", "conan", "dotnet", "gem",
    "gradle", "make", "meson", "mix", "mvn", "npm", "npx", "nuget",
    "pip", "pip3", "pnpm", "poetry", "raco", "rake", "sbt", "stack",
    "uv", "yarn", "git", "hg", "svn", "pytest", "tox", "nox", "jest",
    "mocha", "go", "gotestsum", "prove", "rspec", "unittest", "vitest",
    "bandit", "black", "flake8", "mypy", "pylint", "pyright", "ruff",
    "cat", "cp", "cut", "date", "diff", "echo", "find", "grep", "head",
    "id", "ls", "mkdir", "mv", "pwd", "rm", "sed", "sort", "tail", "tar",
    "tee", "test", "touch", "tr", "uniq", "wc", "which", "whoami",
    "zip",
}

_uv_run_child_commands := {"bandit", "black", "flake8", "mypy", "pylint", "pyright", "pytest", "ruff"}

_command_allowlist_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    not _bare_allowed_command(argv[0])
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
    _rm_flag_present(argv, "r")
    _rm_flag_present(argv, "f")
    msg := sprintf("Destructive rm flags in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "rm"
    some arg in argv
    arg == "--no-preserve-root"
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
    _sed_file_script(argv)
    msg := sprintf("Sed file script execution cannot be inspected safely: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    some index, arg in argv
    script := _sed_script(argv, index)
    _sed_script_executes(script)
    msg := sprintf("Sed command-execution primitive: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "zip"
    some arg in argv
    _zip_test_command_option(arg)
    msg := sprintf("Zip test command execution primitive: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    some arg in argv
    arg in {"-i", "--in-place", "--i", "--in", "--inp"}
    msg := sprintf("Sed in-place file write cannot be inspected safely: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    some arg in argv
    startswith(arg, "--in-p")
    msg := sprintf("Sed in-place file write cannot be inspected safely: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    some arg in argv
    startswith(arg, "-i")
    not startswith(arg, "--")
    msg := sprintf("Sed in-place file write cannot be inspected safely: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    some index, arg in argv
    script := _sed_script(argv, index)
    _sed_script_file_io(script)
    msg := sprintf("Sed file input/output primitive: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "sed"
    not _has_parsed_command(cmd)
    regex.match(`(?i)(^|[[:space:]])[^[:space:]]*[rRwW][[:space:]]+[^[:space:]]+`, cmd)
    msg := sprintf("Sed file input/output primitive: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some index, arg in argv
    _tar_extract_operation(arg)
    msg := sprintf("Tar archive extraction writes untrusted files: %s", [cmd])
}

_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some index, arg in argv
    index == 1
    _tar_old_style_extract_operation(arg)
    msg := sprintf("Tar archive extraction writes untrusted files: %s", [cmd])
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
    lower(arg) in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint", "-fprint0", "-fprintf"}
    msg := sprintf("Dangerous find action in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some arg in argv
    some flag in _tar_dangerous_flags
    startswith(lower(arg), flag)
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some index, arg in argv
    _tar_short_dangerous_flag(arg)
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "tar"
    some index, arg in argv
    index == 1
    _tar_old_style_short_dangerous_flag(arg)
    msg := sprintf("Dangerous tar option in command: %s", [cmd])
}

_destructive_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some subcommand_index, subcommand in argv
    subcommand_index > 0
    subcommand == "clean"
    some flag_index, flag in argv
    flag_index > subcommand_index
    _git_clean_dangerous_flag(flag)
    msg := sprintf("Dangerous git clean flags in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, arg in argv
    index >= 1
    _git_config_key(argv, index) != ""
    lower(_git_config_key(argv, index)) in {
        "core.sshcommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "core.gitproxy",
        "core.hookspath",
        "credential.helper",
        "include.path",
        "protocol.ext.allow",
    }
    msg := sprintf("Dangerous git config override in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index
    arg := argv[index]
    index >= 1
    _git_config_env_key(argv, index) != ""
    lower(_git_config_env_key(argv, index)) in {
        "core.sshcommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "core.gitproxy",
        "core.hookspath",
        "credential.helper",
        "include.path",
        "protocol.ext.allow",
    }
    msg := sprintf("Dangerous git config-env override in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index
    arg := argv[index]
    index >= 1
    arg == "config"
    index == _git_subcommand_index(argv)
    key := _git_config_subcommand_key(argv, index)
    key != ""
    lower(key) in {
        "core.sshcommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "core.gitproxy",
        "core.hookspath",
        "credential.helper",
        "include.path",
        "protocol.ext.allow",
    }
    msg := sprintf("Dangerous git config subcommand in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_key(argv, index)
    _is_executable_git_config_key(key)
    msg := sprintf("Executable git config key in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_env_key(argv, index)
    _is_executable_git_config_key(key)
    msg := sprintf("Executable git config-env key in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, arg in argv
    index >= 1
    arg == "config"
    index == _git_subcommand_index(argv)
    key := _git_config_subcommand_key(argv, index)
    _is_executable_git_config_key(key)
    msg := sprintf("Executable git config subcommand key in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_key(argv, index)
    startswith(lower(key), "alias.")
    value := _git_config_value(argv, index)
    startswith(trim(value, " \t"), "!")
    msg := sprintf("Shell git alias config in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_env_key(argv, index)
    startswith(lower(key), "alias.")
    msg := sprintf("Shell git alias config-env in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, arg in argv
    index >= 1
    arg == "config"
    index == _git_subcommand_index(argv)
    key := _git_config_subcommand_key(argv, index)
    startswith(lower(key), "alias.")
    value := _git_config_subcommand_value(argv, index)
    startswith(trim(value, " \t"), "!")
    msg := sprintf("Shell git alias config subcommand in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_key(argv, index)
    _git_remote_command_config_key(key)
    msg := sprintf("Git remote command config in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, _ in argv
    index >= 1
    key := _git_config_env_key(argv, index)
    _git_remote_command_config_key(key)
    msg := sprintf("Git remote command config-env in command: %s", [cmd])
}

_git_config_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some index, arg in argv
    index >= 1
    arg == "config"
    index == _git_subcommand_index(argv)
    key := _git_config_subcommand_key(argv, index)
    _git_remote_command_config_key(key)
    msg := sprintf("Git remote command config subcommand in command: %s", [cmd])
}

_git_transport_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some arg in argv
    _git_upload_pack_option(argv, arg)
    msg := sprintf("Git upload-pack command option: %s", [cmd])
}

_git_transport_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some arg in argv
    startswith(lower(arg), "ext::")
    msg := sprintf("Git ext transport command: %s", [cmd])
}

_git_transport_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some arg in argv
    _git_exec_path_option(arg)
    msg := sprintf("Git executable path command option: %s", [cmd])
}

_git_transport_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    some arg in argv
    _git_transport_helper_option(argv, arg)
    msg := sprintf("Git transport helper override: %s", [cmd])
}

_git_transport_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    _git_bisect_run(argv)
    msg := sprintf("Git bisect run command: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) in {"difftool", "mergetool", "filter-branch"}
    msg := sprintf("Git subcommand executes external commands: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) == "rebase"
    some index, arg in argv
    index > subcommand_index
    startswith(split(lower(arg), "=")[0], "-x")
    msg := sprintf("Git rebase command execution option: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) == "submodule"
    some index, arg in argv
    index > subcommand_index
    lower(arg) == "foreach"
    msg := sprintf("Git submodule foreach command: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) == "apply"
    some index, arg in argv
    index > subcommand_index
    _git_long_option_matches(split(lower(arg), "=")[0], "--unsafe-paths")
    msg := sprintf("Git apply unsafe path option: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) == "clone"
    some index, arg in argv
    index > subcommand_index
    _git_long_option_matches(split(lower(arg), "=")[0], "--template")
    msg := sprintf("Git clone template hook option: %s", [cmd])
}

_git_command_execution_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    subcommand_index := _git_subcommand_index(argv)
    subcommand_index > 0
    lower(argv[subcommand_index]) == "config"
    some index, arg in argv
    index > subcommand_index
    _git_config_edit_option(arg)
    msg := sprintf("Git config editor option: %s", [cmd])
}

_git_force_push_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) == "git"
    object.get(_git, "force_push", "deny") == "deny"
    _git_push_force_option(argv)
    msg := sprintf("Force push is denied by project profile: %s", [cmd])
}

_control_file_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) in {"git", "cp", "mv", "mkdir", "tee", "touch", "tar", "unzip", "zip", "sed", "go", "npm", "pytest", "uv"}
    some arg in argv
    _is_git_control_file_path(arg)
    msg := sprintf("Command targets protected git control file: %s", [cmd])
}

_control_file_violation contains msg if {
    some cmd in _commands
    argv := _command_argv(cmd)
    count(argv) > 0
    _base_command(argv[0]) in {"git", "cp", "mv", "mkdir", "tee", "touch", "tar", "unzip", "zip", "sed", "go", "npm", "pytest", "uv"}
    some arg in argv
    path := _git_control_file_option_path(argv, arg)
    _is_git_control_file_path(path)
    msg := sprintf("Command targets protected git control file: %s", [cmd])
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

_has_parsed_command(cmd) if {
    some record in _parsed_commands
    object.get(record, "raw", "") == cmd
}

_git_clean_dangerous_flag(arg) if {
    lower(arg) in {"-f", "--force", "-x", "-d"}
}

_tar_dangerous_flags := {
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

_tar_short_dangerous_flag(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(arg, "F")
}

_tar_extract_operation(arg) if {
    lower(arg) in {"--extract", "--get"}
}

_tar_extract_operation(arg) if {
    startswith(lower(arg), "--extr")
}

_tar_extract_operation(arg) if {
    startswith(lower(arg), "--ge")
}

_tar_extract_operation(arg) if {
    startswith(lower(arg), "--ext")
}

_tar_extract_operation(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(lower(arg), "x")
}

_tar_old_style_extract_operation(arg) if {
    not startswith(arg, "-")
    substring(lower(arg), 0, 1) in {"a", "c", "d", "f", "r", "t", "u", "v", "x"}
    contains(lower(arg), "x")
}

_tar_short_dangerous_flag(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(arg, "I")
}

_tar_short_dangerous_flag(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(arg, "P")
}

_tar_old_style_short_dangerous_flag(arg) if {
    not startswith(arg, "-")
    substring(lower(arg), 0, 1) in {"a", "c", "d", "f", "r", "t", "u", "v", "x"}
    some flag in {"F", "I", "P"}
    contains(arg, flag)
}

_git_upload_pack_option(argv, arg) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "clone"
    lower(arg) == "-u"
}

_git_upload_pack_option(argv, arg) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "clone"
    startswith(lower(arg), "-u")
    not lower(arg) == "-u"
}

_git_upload_pack_option(_, arg) if {
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--upload-pack")
}

_git_exec_path_option(arg) if {
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--exec-path")
}

_git_transport_helper_option(argv, arg) if {
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--receive-pack")
}

_git_transport_helper_option(argv, arg) if {
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--exec")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    option := split(lower(arg), "=")[0]
    option in {"--force", "--force-with-lease", "-f"}
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--force")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--force-with-lease")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--mirror")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(lower(arg), "f")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    option := split(lower(arg), "=")[0]
    _git_long_option_matches(option, "--delete")
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    startswith(arg, "+")
    count(arg) > 1
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    startswith(arg, ":")
    count(arg) > 1
}

_git_push_force_option(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "push"
    some index, arg in argv
    index > subcommand_index
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(lower(arg), "d")
}

_git_bisect_run(argv) if {
    subcommand_index := _git_subcommand_index(argv)
    lower(argv[subcommand_index]) == "bisect"
    some index, arg in argv
    index > subcommand_index
    lower(arg) == "run"
}

_git_remote_command_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "remote.")
    endswith(normalized, ".uploadpack")
}

_git_remote_command_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "remote.")
    endswith(normalized, ".receivepack")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "filter.")
    endswith(normalized, ".clean")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "filter.")
    endswith(normalized, ".smudge")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "filter.")
    endswith(normalized, ".process")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "diff.")
    endswith(normalized, ".textconv")
}

_is_executable_git_config_key(key) if {
    lower(key) == "diff.external"
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "merge.")
    endswith(normalized, ".driver")
}

_is_executable_git_config_key(key) if {
    lower(key) in {"core.askpass", "gpg.program", "sequence.editor"}
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "includeif.")
    endswith(normalized, ".path")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "difftool.")
    endswith(normalized, ".cmd")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "mergetool.")
    endswith(normalized, ".cmd")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "submodule.")
    endswith(normalized, ".update")
}

_is_executable_git_config_key(key) if {
    lower(key) in {"gpg.ssh.defaultkeycommand", "gpg.ssh.program"}
}

_is_executable_git_config_key(key) if {
    lower(key) == "core.alternaterefscommand"
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "credential.")
    endswith(normalized, ".helper")
}

_is_executable_git_config_key(key) if {
    normalized := lower(key)
    startswith(normalized, "diff.")
    endswith(normalized, ".command")
}

_zip_test_command_option(arg) if {
    startswith(lower(arg), "-")
    not startswith(lower(arg), "--")
    contains(substring(lower(arg), 1, -1), "tt")
}

_zip_test_command_option(arg) if {
    parts := split(lower(arg), "=")
    option := parts[0]
    option == "--test-command"
}

_zip_test_command_option(arg) if {
    parts := split(lower(arg), "=")
    option := parts[0]
    startswith(option, "--test-c")
    startswith("--test-command", option)
}

_git_clean_dangerous_flag(arg) if {
    startswith(arg, "-")
    not startswith(arg, "--")
    some flag in {"f", "x", "d"}
    contains(lower(arg), flag)
}

_sed_file_script(argv) if {
    some arg in argv
    arg == "-f"
}

_sed_file_script(argv) if {
    some arg in argv
    arg == "--file"
}

_sed_file_script(argv) if {
    some arg in argv
    startswith(arg, "-f")
    arg != "-f"
}

_sed_file_script(argv) if {
    some arg in argv
    startswith(arg, "--file=")
}

_sed_file_script(argv) if {
    some arg in argv
    startswith(arg, "--fi")
}

_sed_file_script(argv) if {
    some arg in argv
    startswith(arg, "-")
    not startswith(arg, "--")
    contains(arg, "f")
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
    startswith(lower(arg), "--config=")
    assignment := substring(arg, 9, -1)
    parts := split(assignment, "=")
    key := parts[0]
}

_git_config_env_key(argv, index) := key if {
    arg := argv[index]
    startswith(lower(arg), "--config-env=")
    parts := split(arg, "=")
    count(parts) >= 3
    key := parts[1]
}

_git_config_env_key(argv, index) := key if {
    argv[index] == "--config-env"
    assignment := argv[index + 1]
    parts := split(assignment, "=")
    count(parts) >= 2
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

_git_config_value(argv, index) := value if {
    arg := argv[index]
    arg in {"-c", "--config"}
    assignment := argv[index + 1]
    equals_index := indexof(assignment, "=")
    equals_index >= 0
    value := substring(assignment, equals_index + 1, -1)
}

_git_config_value(argv, index) := value if {
    arg := argv[index]
    startswith(lower(arg), "--config=")
    assignment := substring(arg, 9, -1)
    equals_index := indexof(assignment, "=")
    equals_index >= 0
    value := substring(assignment, equals_index + 1, -1)
}

_git_config_value(argv, index) := value if {
    arg := argv[index]
    startswith(lower(arg), "-c")
    not arg in {"-c", "--config"}
    assignment := substring(arg, 2, -1)
    equals_index := indexof(assignment, "=")
    equals_index >= 0
    value := substring(assignment, equals_index + 1, -1)
}

# #309: git config [<options>] <key> <value> persists a config key. Locate the
# first non-option token after "config", treating the "set" command and
# --file/-f/--blob/--type options and their unambiguous abbreviations as
# non-key tokens.
_git_config_subcommand_key(argv, config_index) := key if {
    candidates := [i |
        some i, arg in argv
        i > config_index
        not startswith(arg, "-")
        arg != "set"
        not _git_config_option_argument(argv, config_index, i)
    ]
    count(candidates) > 0
    key := _git_config_key_name(argv[candidates[0]])
}

_git_config_subcommand_value(argv, config_index) := value if {
    candidates := [i |
        some i, arg in argv
        i > config_index
        not startswith(arg, "-")
        arg != "set"
        not _git_config_option_argument(argv, config_index, i)
    ]
    count(candidates) > 0
    key_index := candidates[0]
    key_token := argv[key_index]
    equals_index := indexof(key_token, "=")
    equals_index < 0
    value := argv[key_index + 1]
}

_git_config_subcommand_value(argv, config_index) := value if {
    candidates := [i |
        some i, arg in argv
        i > config_index
        not startswith(arg, "-")
        arg != "set"
        not _git_config_option_argument(argv, config_index, i)
    ]
    count(candidates) > 0
    key_token := argv[candidates[0]]
    equals_index := indexof(key_token, "=")
    equals_index >= 0
    value := substring(key_token, equals_index + 1, -1)
}

_git_config_edit_option(arg) if {
    lower(arg) == "-e"
}

_git_config_edit_option(arg) if {
    option := lower(arg)
    count(option) >= 3
    startswith(option, "--")
    startswith("--edit", option)
}

_git_config_key_name(token) := key if {
    parts := split(token, "=")
    key := parts[0]
}

_git_config_option_argument(argv, config_index, i) if {
    some j
    j > config_index
    j < i
    argv[j] in {"--file", "--fil", "-f", "--blob", "--blo", "--type", "--typ", "--t", "--ty", "-t", "--value", "--val", "--default", "--def", "--comment", "--com", "--co"}
    i == j + 1
}

_git_global_options_with_values := {
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

_git_global_option_value(argv, index) if {
    some option_index, option in argv
    option_index >= 1
    option_index < index
    option in _git_global_options_with_values
    index == option_index + 1
}

_git_prior_non_subcommand_argument(argv, index) if {
    some prior, arg in argv
    prior >= 1
    prior < index
    not startswith(arg, "-")
    not _git_global_option_value(argv, prior)
}

_git_subcommand_index(argv) := index if {
    some index, arg in argv
    index >= 1
    not startswith(arg, "-")
    not _git_global_option_value(argv, index)
    not _git_prior_non_subcommand_argument(argv, index)
}

_git_long_option_matches(option, canonical) if {
    startswith(option, "--")
    count(option) >= 3
    startswith(canonical, option)
}

_uv_run_child_index(argv) := index if {
    _base_command(argv[0]) == "uv"
    some run_index, arg in argv
    run_index >= 1
    arg == "run"
    not _uv_prior_run(argv, run_index)
    index := run_index + 1
    not startswith(argv[index], "-")
}

_uv_run_child_index(argv) := index if {
    _base_command(argv[0]) == "uv"
    some run_index, arg in argv
    run_index >= 1
    arg == "run"
    not _uv_prior_run(argv, run_index)
    index := run_index + 2
    argv[run_index + 1] == "--"
    not startswith(argv[index], "-")
}

_uv_run_child_index(argv) := -1 if {
    _base_command(argv[0]) == "uv"
    some run_index, arg in argv
    run_index >= 1
    arg == "run"
    not _uv_prior_run(argv, run_index)
    not _uv_run_child_index_explicit(argv, run_index)
}

_uv_run_child_index_explicit(argv, run_index) if {
    index := run_index + 1
    not startswith(argv[index], "-")
}

_uv_run_child_index_explicit(argv, run_index) if {
    index := run_index + 2
    argv[run_index + 1] == "--"
    not startswith(argv[index], "-")
}

_uv_prior_run(argv, run_index) if {
    some prior, arg in argv
    prior >= 1
    prior < run_index
    arg == "run"
}

_git_control_file_option_path(argv, arg) := path if {
    some prefix in {
        "--target-directory=",
        "--directory=",
        "--files-from=",
        "--output=",
        "--git-dir=",
        "--work-tree=",
        "--file=",
        "-t",
    }
    startswith(lower(arg), lower(prefix))
    path := trim(substring(arg, count(prefix), -1), "/=")
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    child_index := _uv_run_child_index(argv)
    _base_command(argv[child_index]) == "pytest"
    some index
    index > child_index
    argv[index] == arg
    parts := split(lower(arg), "=")
    option := parts[0]
    some canonical in {"--basetemp", "--junitxml", "--junit-xml", "--log-file", "--debug"}
    count(option) > 2
    startswith(canonical, option)
    count(parts) > 1
    path := substring(arg, indexof(arg, "=") + 1, -1)
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    _base_command(argv[0]) == "npm"
    parts := split(lower(arg), "=")
    option := parts[0]
    some canonical in {
        "--prefix",
        "--userconfig",
        "--globalconfig",
        "--cache",
        "--logs-dir",
    }
    count(option) > 2
    startswith(canonical, option)
    count(parts) > 1
    path := substring(arg, indexof(arg, "=") + 1, -1)
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    count(arg) > 2
    startswith(arg, "-C")
    path := trim(substring(arg, 2, -1), "/=")
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    _base_command(argv[0]) == "git"
    count(arg) > 2
    startswith(arg, "-f")
    not startswith(arg, "--")
    path := trim(substring(arg, 2, -1), "=/")
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    argv[index] == "-f"
    _base_command(argv[0]) == "git"
    index + 1 < count(argv)
    path := trim(argv[index + 1], "/")
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    _base_command(argv[0]) in {"git", "go"}
    count(arg) > 2
    substring(lower(arg), 0, 2) == "-o"
    path := trim(substring(arg, 2, -1), "=")
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    some index
    index >= 1
    argv[index] == arg
    _base_command(argv[0]) == "pytest"
    parts := split(lower(arg), "=")
    option := parts[0]
    some canonical in {
        "--basetemp",
        "--junitxml",
        "--junit-xml",
        "--result-log",
        "--log-file",
        "--debug",
    }
    count(option) > 2
    startswith(canonical, option)
    count(parts) > 1
    path := substring(arg, indexof(arg, "=") + 1, -1)
    path != ""
}

_git_control_file_option_path(argv, arg) := path if {
    _base_command(argv[0]) == "tar"
    not startswith(arg, "--")
    option_chars := trim(arg, "-")
    substring(lower(option_chars), 0, 1) in {"a", "c", "d", "f", "r", "t", "u", "x"}
    file_index := indexof(lower(arg), "f")
    file_index >= 0
    path := trim(substring(arg, file_index + 1, -1), "=")
    path != ""
}

_is_git_control_file_path(path) if {
    parts := split(trim(path, "/"), "/")
    some part in parts
    part == ".git"
}

_base_command(token) := base if {
    parts := split(lower(token), "/")
    base := parts[count(parts) - 1]
}

_bare_allowed_command(token) if {
    lower(token) in _allowed_commands
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

_rm_flag_present(argv, flag) if {
    some arg in argv
    startswith(arg, "-")
    contains(lower(arg), flag)
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    argv[index] in {"-e", "--expression"}
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    argv[index] == "--expr"
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    startswith(argv[index], "--expression=")
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    startswith(argv[index], "--expr=")
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    startswith(argv[index], "--e")
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    startswith(argv[index], "-e")
    not argv[index] == "-e"
    not startswith(argv[index], "--")
}

_sed_has_explicit_expression(argv) if {
    some index
    index >= 1
    startswith(argv[index], "-")
    not startswith(argv[index], "--")
    contains(argv[index], "e")
}

_sed_has_prior_bare_argument(argv, index) if {
    some prior_index, prior_arg in argv
    prior_index >= 1
    prior_index < index
    not startswith(prior_arg, "-")
}

_sed_bare_script(argv, index) if {
    index >= 1
    not startswith(argv[index], "-")
    not _sed_has_explicit_expression(argv)
    not _sed_has_prior_bare_argument(argv, index)
}

_sed_script(argv, index) := script if {
    argv[index] in {"-e", "--expression", "--expr", "-f", "--file"}
    script := argv[index + 1]
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "--expression=")
    script := substring(argv[index], 13, -1)
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "-")
    not startswith(argv[index], "--")
    contains(argv[index], "e")
    attached := substring(argv[index], indexof(argv[index], "e") + 1, -1)
    attached != ""
    script := attached
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "-")
    not startswith(argv[index], "--")
    contains(argv[index], "e")
    attached := substring(argv[index], indexof(argv[index], "e") + 1, -1)
    attached == ""
    script := argv[index + 1]
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "--expr=")
    script := substring(argv[index], 7, -1)
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "--e")
    not contains(argv[index], "=")
    script := argv[index + 1]
}

_sed_script(argv, index) := script if {
    startswith(argv[index], "--e")
    contains(argv[index], "=")
    equals_index := indexof(argv[index], "=")
    script := substring(argv[index], equals_index + 1, -1)
}

_sed_script(argv, index) := script if {
    _sed_bare_script(argv, index)
    script := argv[index]
}

_sed_backslash_run_length(script, p) := n if {
    p > 0
    n := count([q |
        some q in numbers.range(0, p - 1)
        every r in numbers.range(q, p - 1) {
            substring(script, r, 1) == "\\"
        }
    ])
}

_sed_backslash_run_length(script, p) := 0 if {
    p <= 0
}

_sed_is_escaped(script, p) if {
    n := _sed_backslash_run_length(script, p)
    n % 2 == 1
}

_sed_unescaped_delimiters(script, delimiter, after) := sorted if {
    positions := [p |
        some p in numbers.range(after + 1, count(script) - 1)
        substring(script, p, 1) == delimiter
        not _sed_is_escaped(script, p)
    ]
    sorted := sort(positions)
}

_sed_is_digit(char) if { char >= "0"; char <= "9" }

_sed_skip_digits(script, start) := end if {
    digits := [p |
        some p in numbers.range(start, count(script) - 1)
        every q in numbers.range(start, p) {
            _sed_is_digit(substring(script, q, 1))
        }
    ]
    count(digits) > 0
    end := digits[count(digits) - 1] + 1
}

_sed_skip_digits(script, start) := start if {
    not _sed_has_contiguous_digit(script, start)
}

_sed_has_contiguous_digit(script, start) if {
    some p in numbers.range(start, count(script) - 1)
    every q in numbers.range(start, p) {
        _sed_is_digit(substring(script, q, 1))
    }
}

_sed_skip_step_suffix(script, start) := end if {
    start < count(script)
    substring(script, start, 1) == "~"
    end := _sed_skip_digits(script, start + 1)
}

_sed_skip_step_suffix(script, start) := start if {
    not start < count(script)
}

_sed_skip_step_suffix(script, start) := start if {
    start < count(script)
    not substring(script, start, 1) == "~"
}

_sed_skip_address(script, start) := end if {
    start < count(script)
    char := substring(script, start, 1)
    _sed_is_digit(char)
    digits_end := _sed_skip_digits(script, start)
    end := _sed_skip_step_suffix(script, digits_end)
}

_sed_skip_address(script, start) := end if {
    start < count(script)
    substring(script, start, 1) == "$"
    end := start + 1
}

_sed_skip_address(script, start) := end if {
    start < count(script)
    substring(script, start, 1) == "/"
    positions := _sed_unescaped_delimiters(script, "/", start)
    count(positions) >= 1
    end := positions[0] + 1
}

_sed_skip_address(script, start) := end if {
    start < count(script)
    substring(script, start, 1) == "\\"
    delimiter := substring(script, start + 1, 1)
    positions := _sed_unescaped_delimiters(script, delimiter, start + 1)
    count(positions) >= 1
    end := positions[0] + 1
}

_sed_skip_address(script, start) := start if {
    start >= count(script)
}

_sed_skip_address(script, start) := start if {
    start < count(script)
    char := substring(script, start, 1)
    not _sed_is_digit(char)
    not char == "$"
    not char == "/"
    not char == "\\"
}

_sed_skip_addresses(script, start) := end if {
    first := _sed_skip_address(script, start)
    first >= count(script)
    end := first
}

_sed_skip_addresses(script, start) := end if {
    first := _sed_skip_address(script, start)
    first < count(script)
    not substring(script, first, 1) == ","
    end := first
}

_sed_skip_addresses(script, start) := end if {
    first := _sed_skip_address(script, start)
    first < count(script)
    substring(script, first, 1) == ","
    end := _sed_skip_address(script, first + 1)
}

_sed_is_whitespace(char) if { char == " " }
_sed_is_whitespace(char) if { char == "\t" }

_sed_skip_whitespace(script, start) := end if {
    non_ws := [p |
        some p in numbers.range(start, count(script) - 1)
        not _sed_is_whitespace(substring(script, p, 1))
    ]
    count(non_ws) > 0
    end := non_ws[0]
}

_sed_skip_whitespace(script, start) := end if {
    non_ws := [p |
        some p in numbers.range(start, count(script) - 1)
        not _sed_is_whitespace(substring(script, p, 1))
    ]
    count(non_ws) == 0
    end := count(script)
}

_sed_skip_ws_negation(script, start) := end if {
    after_ws := _sed_skip_whitespace(script, start)
    after_ws < count(script)
    substring(script, after_ws, 1) == "!"
    end := _sed_skip_whitespace(script, after_ws + 1)
}

_sed_skip_ws_negation(script, start) := end if {
    after_ws := _sed_skip_whitespace(script, start)
    not after_ws < count(script)
    end := after_ws
}

_sed_skip_ws_negation(script, start) := end if {
    after_ws := _sed_skip_whitespace(script, start)
    after_ws < count(script)
    not substring(script, after_ws, 1) == "!"
    end := after_ws
}

_sed_strip_quotes(script) := stripped if {
    count(script) >= 2
    first := substring(script, 0, 1)
    last := substring(script, count(script) - 1, 1)
    first == last
    first == "\""
    stripped := substring(script, 1, count(script) - 2)
}

_sed_strip_quotes(script) := stripped if {
    count(script) >= 2
    first := substring(script, 0, 1)
    last := substring(script, count(script) - 1, 1)
    first == last
    first == "'"
    stripped := substring(script, 1, count(script) - 2)
}

_sed_strip_quotes(script) := script if {
    count(script) < 2
}

_sed_strip_quotes(script) := script if {
    count(script) >= 2
    first := substring(script, 0, 1)
    last := substring(script, count(script) - 1, 1)
    not first == last
}

_sed_strip_quotes(script) := script if {
    count(script) >= 2
    first := substring(script, 0, 1)
    last := substring(script, count(script) - 1, 1)
    first == last
    not first == "\""
    not first == "'"
}

_sed_command_index(script, cmd) := index if {
    stripped := _sed_strip_quotes(script)
    after_addr := _sed_skip_addresses(stripped, 0)
    after_prefix := _sed_skip_ws_negation(stripped, after_addr)
    index := after_prefix
    index < count(stripped)
    substring(stripped, index, 1) == cmd
}

_sed_substitution_flags(script) := flags if {
    stripped := _sed_strip_quotes(script)
    cmd_index := _sed_command_index(stripped, "s")
    cmd_index >= 0
    count(stripped) > cmd_index + 2
    delimiter := substring(stripped, cmd_index + 1, 1)
    delimiter != ""
    positions := _sed_unescaped_delimiters(stripped, delimiter, cmd_index + 1)
    count(positions) >= 2
    flags := substring(stripped, positions[1] + 1, -1)
}

_sed_script_executes(script) if {
    regex.match(`(?i)(^|[;\n])[[:space:]]*[^;\n]*e([[:space:]]|$|;)`, script)
}

_sed_after_range_prefix(script) := after if {
    stripped := _sed_strip_quotes(script)
    after_ws := _sed_skip_whitespace(stripped, 0)
    after_addr := _sed_skip_addresses(stripped, after_ws)
    after := _sed_skip_ws_negation(stripped, after_addr)
}

_sed_script_file_io(script) if {
    flags := _sed_substitution_flags(script)
    contains(lower(flags), "w")
}

_sed_script_file_io(script) if {
    stripped := _sed_strip_quotes(script)
    after := _sed_after_range_prefix(script)
    after < count(stripped)
    cmd := substring(stripped, after, 1)
    cmd in {"r", "R", "w", "W"}
}




_sed_script_executes(script) if {
    flags := _sed_substitution_flags(script)
    contains(lower(flags), "e")
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
