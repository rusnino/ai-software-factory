package governance.approve_test

import rego.v1

_base_input := {
    "proposed_by": "agent-1",
    "approval_type": "execution",
    "has_objective": true,
    "has_acceptance": true,
    "approval": {"actor": "human-1"},
    "execution": {
        "harness": "opencode",
        "role": "worker",
    },
    "commands": [],
    "allowed_harnesses": ["opencode"],
    "security": {},
    "git": {},
}

test_sudo_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sudo whoami"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "privilege")
}

test_unallowlisted_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["docker run alpine"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "allowlist")
}

test_mkfs_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["mkfs.ext4 /dev/sdb"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "destructive")
}

test_sed_execution_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sed '1e touch /tmp/pwned' file.txt"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_sed_substitution_execution_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sed -e \"s/line/id/e\" file.txt"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_nul_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["echo safe\u0000rm -rf /"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "control")
}

test_harness_role_and_resource_caps_are_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "execution": {
                "harness": "opencode",
                "role": "planner",
                "timeout_minutes": 120,
                "max_retries": 5,
            },
            "harness_roles": {"opencode": ["worker"]},
            "profile_execution": {
                "timeout_minutes": 60,
                "max_retries": 2,
            },
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "role")
    some cap_violation in decision.violations
    contains(lower(cap_violation), "timeout")
}

test_git_clean_x_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["git clean -x"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "git clean")
}

test_git_clean_d_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["git clean -d"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "git clean")
}

test_git_clean_force_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["git clean --force"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "git clean")
}

test_rm_uppercase_recursive_flag_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["rm -Rf /tmp/work"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "destructive")
}

test_rm_uppercase_force_flag_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["rm -RF /tmp/work"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "destructive")
}

test_tar_uppercase_remove_files_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["tar --REMOVE-FILES archive.tar"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "dangerous")
}

test_find_uppercase_delete_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["find . -DELETE"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "dangerous")
}

test_git_config_override_forms_are_denied if {
    some command in [
        "git -c core.sshCommand=touch status",
        "git --config core.editor=vim status",
        "/usr/bin/git -c core.pager=less status",
    ]
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": [command]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "config")
}

test_forbidden_path_sibling_is_allowed if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": ["echo /tmp/secret_backup"],
            "forbidden_paths": ["/tmp/secret"],
        },
    )

    decision.allow == true
}

test_command_newline_is_denied_even_with_parsed_argv if {
    command := "echo safe\necho still-safe"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["echo", "safe", "echo", "still-safe"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "control")
}

test_missing_completeness_fact_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"has_objective": false},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "objective")
}
