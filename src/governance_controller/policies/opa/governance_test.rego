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

test_path_qualified_allowlisted_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["./sed file.txt"]},
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

test_sed_range_execution_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sed '1,2e touch /tmp/pwned' file.txt"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_sed_regex_address_execution_command_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sed '/./e touch /tmp/pwned' file.txt"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_sed_attached_and_abbreviated_options_are_denied if {
    cases := [
        "sed -ne's/foo/bar/e' file.txt",
        "sed -nf script.sed file.txt",
        "sed --expr='s/foo/bar/e' file.txt",
        "sed --expr 's/foo/bar/e' file.txt",
        "sed --e='s/foo/bar/e' file.txt",
        "sed --e 's/foo/bar/e' file.txt",
    ]
    every command in cases {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_sed_abbreviated_expression_without_equals_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["sed --expr 's/foo/bar/e' file.txt"]},
    )

    decision.allow == false
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

test_parsed_safe_sed_expression_is_allowed if {
    command := "sed -e 's/foo/bar/g' file.txt"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-e", "s/foo/bar/g", "file.txt"],
                "error": "",
            }],
        },
    )

    decision.allow == true
}

test_parsed_safe_sed_filename_is_allowed if {
    command := "sed -n '1,10p' source"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "1,10p", "source"],
                "error": "",
            }],
        },
    )

    decision.allow == true
}

test_parsed_sed_substitution_execution_is_denied if {
    command := "sed 's/foo/bar/e' file.txt"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "s/foo/bar/e", "file.txt"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_parsed_sed_extended_execution_forms_are_denied if {
    command := "sed '1,2s/foo/bar/ep' file.txt"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "1,2s/foo/bar/ep", "file.txt"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_parsed_sed_file_script_is_denied if {
    command := "sed -f script.sed file.txt"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-f", "script.sed", "file.txt"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "execution")
}

test_parsed_uppercase_split_rm_flags_are_denied if {
    command := "rm -R -F /tmp/work"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["rm", "-R", "-F", "/tmp/work"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "destructive")
}

test_parsed_long_recursive_rm_matches_embedded_policy if {
    command := "rm --recursive /tmp/work"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["rm", "--recursive", "/tmp/work"],
                "error": "",
            }],
        },
    )

    decision.allow == true
}

test_parsed_git_global_option_clean_is_denied if {
    command := "git -C /tmp/repo clean -fdx"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["git", "-C", "/tmp/repo", "clean", "-fdx"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "git clean")
}

test_parsed_git_uppercase_compact_clean_is_denied if {
    command := "git -C /tmp/repo clean -FDX"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["git", "-C", "/tmp/repo", "clean", "-FDX"],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "git clean")
}

test_parsed_hardened_commands_are_denied if {
    cases := [
        {
            "command": "rm -Rf /tmp/work",
            "argv": ["rm", "-R", "-f", "/tmp/work"],
        },
        {
            "command": "tar --REMOVE-FILES archive.tar",
            "argv": ["tar", "--REMOVE-FILES", "archive.tar"],
        },
        {
            "command": "find . -DELETE",
            "argv": ["find", ".", "-DELETE"],
        },
        {
            "command": "git -c core.sshCommand=touch status",
            "argv": ["git", "-c", "core.sshCommand=touch", "status"],
        },
    ]
    some test_case in cases
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [test_case.command],
            "parsed_commands": [{
                "raw": test_case.command,
                "argv": test_case.argv,
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_tar_execution_hook_options_are_denied if {
    cases := [
        "tar --checkpoint-action=exec=touch -xf archive.tar",
        "tar --checkpoint-a=exec=touch -xf archive.tar",
        "tar -xIcat archive.tar",
        "tar --use=touch -xf archive.tar",
        "tar --use-compress-program=touch -xf archive.tar",
        "tar --info=touch -xf archive.tar",
        "tar --info-script=touch -xf archive.tar",
        "tar --new-v=touch -xf archive.tar",
        "tar --new-volume-script=touch -xf archive.tar",
        "tar --rmt=touch -xf archive.tar",
        "tar --rsh=touch -xf archive.tar",
        "tar --remove -cf archive.tar file.txt",
    ]
    every command in cases {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
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

test_git_config_env_forms_are_denied if {
    some key in [
        "core.sshCommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "credential.helper",
        "include.path",
    ]
    command := sprintf(
        "git --config-env=%s=MALICIOUS_VALUE commit --amend --allow-empty",
        [key],
    )
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": [
                    "git",
                    sprintf("--config-env=%s=MALICIOUS_VALUE", [key]),
                    "commit",
                    "--amend",
                    "--allow-empty",
                ],
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "config")
}

test_git_control_file_targets_are_denied if {
    some test_case in [
        {
            "command": "cp source victim/.git/config",
            "argv": ["cp", "source", "victim/.git/config"],
        },
        {
            "command": "mv source victim/.git/hooks/pre-commit",
            "argv": ["mv", "source", "victim/.git/hooks/pre-commit"],
        },
        {
            "command": "tar -xf malicious.tar",
            "argv": ["tar", "-xf", "malicious.tar"],
        },
    ]
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [test_case.command],
            "parsed_commands": [{
                "raw": test_case.command,
                "argv": test_case.argv,
                "error": "",
            }],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "control")
}

test_sed_file_io_commands_are_denied if {
    some command in [
        "sed -n '1r /tmp/secret' input.txt",
        "sed -n '1R /tmp/secret' input.txt",
        "sed -n '1w /tmp/output' input.txt",
        "sed 's/foo/bar/W /tmp/output' input.txt",
    ]
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": [command]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "file")
}

test_git_add_literal_config_filenames_are_allowed if {
    command := "git add config core.editor"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["git", "add", "config", "core.editor"],
                "error": "",
            }],
        },
    )

    decision.allow == true
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

test_git_config_subcommand_forms_are_denied if {
    keys := [
        "core.sshCommand",
        "core.editor",
        "core.pager",
        "core.fsmonitor",
        "credential.helper",
        "include.path",
    ]
    templates := [
        "git config %s touch",
        "git config --global %s touch",
    ]
    every key in keys {
        every template in templates {
            command := sprintf(template, [key])
            decision := data.governance.approve with input as object.union(
                _base_input,
                {"commands": [command]},
            )
            decision.allow == false
            some violation in decision.violations
            contains(lower(violation), "git config")
        }
    }
}

test_git_config_subcommand_injection_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["git config --global core.editor \"touch /tmp/pwned; true #\""]},
    )

    decision.allow == false
}

test_tar_absolute_names_and_transform_are_denied if {
    every command in [
        "tar -xPf a.tar",
        "tar -x --absolute-names -f a.tar",
        "tar -x --transform=s,x,y, -f a.tar",
        "tar -x --xform=s,x,y, -f a.tar",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )
        decision.allow == false
    }
}

test_find_fprintf_is_denied if {
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": ["find / -maxdepth 1 -fprintf out.txt fmt"]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "dangerous")
}
