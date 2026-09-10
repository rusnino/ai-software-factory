package governance.approve_test

import rego.v1

_base_input := {
    "task_id": "task-1",
    "project_id": "project-1",
    "proposed_by": "agent-1",
    "approval_type": "execution",
    "has_objective": true,
    "has_acceptance": true,
    "approval": {"actor": "human-1", "type": "execution"},
    "execution": {
        "harness": "opencode",
        "role": "worker",
        "uses_docker_socket": false,
        "destructive_shell": false,
        "network_access": "restricted",
        "spawn_subagents": false,
        "force_push": false,
        "signed_commits": false,
        "timeout_minutes": 60,
        "max_retries": 2,
    },
    "commands": [],
    "parsed_commands": [],
    "allowed_harnesses": ["opencode"],
    "forbidden_path_conflicts": [],
    "forbidden_paths": [],
    "security": {
        "docker_socket": "deny",
        "destructive_shell": "deny",
        "network": "restricted",
        "spawn_subagents": "deny",
        "forbidden_paths": [],
    },
    "git": {
        "merge_requires_human": true,
        "force_push": "deny",
        "signed_commits": "optional",
    },
    "profile_execution": {
        "timeout_minutes": 60,
        "max_retries": 2,
    },
    "harness_roles": {"opencode": ["worker"]},
}

test_empty_input_is_denied if {
    decision := data.governance.approve with input as {}

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "input")
}

test_commands_only_input_is_denied if {
    decision := data.governance.approve with input as {"commands": []}

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "input")
}

test_partial_boolean_input_is_denied if {
    decision := data.governance.approve with input as {
        "has_objective": true,
        "has_acceptance": true,
    }

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "input")
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
            {
                "commands": [command],
                "parsed_commands": [{
                    "raw": command,
                    "argv": split(command, " "),
                    "error": "",
                }],
            },
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

test_tar_positional_filenames_are_not_option_clusters if {
    cases := [
        "tar -cf archive.tar fileF",
        "tar -cf archive.tar xfile",
    ]
    every command in cases {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {
                "commands": [command],
                "parsed_commands": [{
                    "raw": command,
                    "argv": split(command, " "),
                    "error": "",
                }],
            },
        )

        decision.allow == true
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

test_git_config_env_space_separated_forms_are_denied if {
    some key in [
        "core.sshCommand",
        "core.fsmonitor",
        "core.editor",
        "core.pager",
        "credential.helper",
        "include.path",
    ]
    some prefix in [
        ["git"],
        ["git", "-C", "repository"],
    ]
    argv := array.concat(prefix, [
        "--config-env",
        sprintf("%s=MALICIOUS_VALUE", [key]),
        "status",
    ])
    command := sprintf("%s", [concat(" ", argv)])
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": argv,
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
            "parsed_commands": [{
                "raw": "echo /tmp/secret_backup",
                "argv": ["echo", "/tmp/secret_backup"],
                "error": "",
            }],
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

test_tar_find_flag_family_with_parsed_argv_is_denied if {
    every test_case in [
        {
            "command": "tar -xPf a.tar",
            "argv": ["tar", "-xPf", "a.tar"],
            "needle": "tar",
        },
        {
            "command": "tar -x --transform=s,x,y, -f a.tar",
            "argv": ["tar", "-x", "--transform=s,x,y,", "-f", "a.tar"],
            "needle": "tar",
        },
        {
            "command": "tar xvPf archive.tar",
            "argv": ["tar", "xvPf", "archive.tar"],
            "needle": "tar",
        },
        {
            "command": "tar --extr archive.tar",
            "argv": ["tar", "--extr", "archive.tar"],
            "needle": "tar",
        },
        {
            "command": "tar vI touch -c -f archive.tar input.txt",
            "argv": [
                "tar",
                "vI",
                "touch",
                "-c",
                "-f",
                "archive.tar",
                "input.txt",
            ],
            "needle": "tar",
        },
        {
            "command": "find / -maxdepth 1 -fprintf out.txt fmt",
            "argv": [
                "find",
                "/",
                "-maxdepth",
                "1",
                "-fprintf",
                "out.txt",
                "fmt",
            ],
            "needle": "find",
        },
    ] {
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
        contains(lower(violation), test_case.needle)
    }
}

test_command_execution_bypasses_are_denied if {
    every test_case in [
        {
            "command": "git -c alias.pwn=!touch /tmp/marker pwn",
            "argv": ["git", "-c", "alias.pwn=!touch", "/tmp/marker", "pwn"],
        },
        {
            "command": "git -c core.hooksPath=/tmp/hooks commit --amend --allow-empty",
            "argv": [
                "git",
                "-c",
                "core.hooksPath=/tmp/hooks",
                "commit",
                "--amend",
                "--allow-empty",
            ],
        },
        {
            "command": "git clone --upload-pack=touch https://example.invalid/repo",
            "argv": [
                "git",
                "clone",
                "--upload-pack=touch",
                "https://example.invalid/repo",
            ],
        },
        {
            "command": "git --config-env core.editor=TERM status",
            "argv": ["git", "--config-env", "core.editor=TERM", "status"],
        },
        {
            "command": "git config --edit",
            "argv": ["git", "config", "--edit"],
        },
        {
            "command": "git config -e",
            "argv": ["git", "config", "-e"],
        },
        {
            "command": "git apply --unsafe-paths patch",
            "argv": ["git", "apply", "--unsafe-paths", "patch"],
        },
        {
            "command": "git clone --template=/tmp/template https://example.invalid/repo",
            "argv": [
                "git",
                "clone",
                "--template=/tmp/template",
                "https://example.invalid/repo",
            ],
        },
        {
            "command": "git clone --u=touch https://example.invalid/repo",
            "argv": [
                "git",
                "clone",
                "--u=touch",
                "https://example.invalid/repo",
            ],
        },
        {
            "command": "git fetch --upl=touch origin",
            "argv": ["git", "fetch", "--upl=touch", "origin"],
        },
        {
            "command": "git push --rece=touch origin HEAD:refs/heads/main",
            "argv": [
                "git",
                "push",
                "--rece=touch",
                "origin",
                "HEAD:refs/heads/main",
            ],
        },
        {
            "command": "git push --e=touch origin HEAD:refs/heads/main",
            "argv": [
                "git",
                "push",
                "--e=touch",
                "origin",
                "HEAD:refs/heads/main",
            ],
        },
        {
            "command": "git apply --uns patch",
            "argv": ["git", "apply", "--uns", "patch"],
        },
        {
            "command": "git clone --te=/tmp/template https://example.invalid/repo",
            "argv": [
                "git",
                "clone",
                "--te=/tmp/template",
                "https://example.invalid/repo",
            ],
        },
        {
            "command": "git config submodule.pwn.update !touch",
            "argv": ["git", "config", "submodule.pwn.update", "!touch"],
        },
        {
            "command": "git config gpg.ssh.defaultKeyCommand touch",
            "argv": ["git", "config", "gpg.ssh.defaultKeyCommand", "touch"],
        },
        {
            "command": "git config gpg.ssh.program touch",
            "argv": ["git", "config", "gpg.ssh.program", "touch"],
        },
        {
            "command": "git config --co comment core.editor touch",
            "argv": [
                "git",
                "config",
                "--co",
                "comment",
                "core.editor",
                "touch",
            ],
        },
        {
            "command": "git -c protocol.ext.allow=always ls-remote ext::touch%20/tmp/marker",
            "argv": [
                "git",
                "-c",
                "protocol.ext.allow=always",
                "ls-remote",
                "ext::touch%20/tmp/marker",
            ],
        },
        {
            "command": "unzip -o payload.zip",
            "argv": ["unzip", "-o", "payload.zip"],
        },
        {
            "command": "sed -n '1w/tmp/marker' input",
            "argv": ["sed", "-n", "1w/tmp/marker", "input"],
        },
        {
            "command": "uv run sh -c 'touch /tmp/marker'",
            "argv": ["uv", "run", "sh", "-c", "touch /tmp/marker"],
        },
        {
            "command": "uv run ./payload",
            "argv": ["uv", "run", "./payload"],
        },
        {
            "command": "uv run /bin/printf payload",
            "argv": ["uv", "run", "/bin/printf", "payload"],
        },
        {
            "command": "uv run uv run /usr/bin/printf NESTED_UNALLOWLISTED",
            "argv": [
                "uv",
                "run",
                "uv",
                "run",
                "/usr/bin/printf",
                "NESTED_UNALLOWLISTED",
            ],
        },
        {
            "command": "uv run rm -rf /",
            "argv": ["uv", "run", "rm", "-rf", "/"],
        },
        {
            "command": "uv run git clean -fdx",
            "argv": ["uv", "run", "git", "clean", "-fdx"],
        },
        {
            "command": "uv run git -c core.sshCommand=touch status",
            "argv": ["uv", "run", "git", "-c", "core.sshCommand=touch", "status"],
        },
        {
            "command": "uv run tar -xf archive.tar",
            "argv": ["uv", "run", "tar", "-xf", "archive.tar"],
        },
        {
            "command": "sed -i.bak s/a/b/ file",
            "argv": ["sed", "-i.bak", "s/a/b/", "file"],
        },
        {
            "command": "sed -ibak s/a/b/ file",
            "argv": ["sed", "-ibak", "s/a/b/", "file"],
        },
        {
            "command": "sed r file input",
            "argv": ["sed", "r", "file", "input"],
        },
        {
            "command": "sed w file input",
            "argv": ["sed", "w", "file", "input"],
        },
        {
            "command": "tar vxf archive.tar",
            "argv": ["tar", "vxf", "archive.tar"],
        },
        {
            "command": "tar fx archive.tar",
            "argv": ["tar", "fx", "archive.tar"],
        },
        {
            "command": "git config -f.git/config advice.detachedHead false",
            "argv": ["git", "config", "-f.git/config", "advice.detachedHead", "false"],
        },
        {
            "command": "git -c credential.https://example.com.helper=!printf username=pwn credential fill",
            "argv": ["git", "-c", "credential.https://example.com.helper=!printf", "username=pwn", "credential", "fill"],
        },
        {
            "command": "git config credential.https://example.com.helper !touch",
            "argv": ["git", "config", "credential.https://example.com.helper", "!touch"],
        },
        {
            "command": "git config diff.pwn.command touch",
            "argv": ["git", "config", "diff.pwn.command", "touch"],
        },
        {
            "command": "git config core.alternateRefsCommand touch",
            "argv": ["git", "config", "core.alternateRefsCommand", "touch"],
        },
    ] {
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

}

test_uv_run_flag_prefixed_child_is_denied if {
    every test_case in [
        {
            "command": "uv run --quiet rm -rf /",
            "argv": ["uv", "run", "--quiet", "rm", "-rf", "/"],
        },
        {
            "command": "uv run --python /tmp/evil pytest",
            "argv": ["uv", "run", "--python", "/tmp/evil", "pytest"],
        },
        {
            "command": "uv run --no-project arbitrary-binary",
            "argv": ["uv", "run", "--no-project", "arbitrary-binary"],
        },
        {
            "command": "uv run -q -- pytest",
            "argv": ["uv", "run", "-q", "--", "pytest"],
        },
    ] {
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
        contains(lower(violation), "uv run child")
    }
}

test_safe_git_config_and_read_only_sed_are_allowed if {
    every test_case in [
        {
            "command": "git -c advice.detachedHead=false status",
            "argv": ["git", "-c", "advice.detachedHead=false", "status"],
        },
        {
            "command": "sed -n '1,10p' source",
            "argv": ["sed", "-n", "1,10p", "source"],
        },
        {
            "command": "sed -n '1,10p' README",
            "argv": ["sed", "-n", "1,10p", "README"],
        },
    ] {
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

        decision.allow == true
    }
}

test_sed_no_space_write_path_conflict_is_denied if {
    command := "sed -n '1w/tmp/blocked/marker' input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "1w/tmp/blocked/marker", "input"],
                "error": "",
            }],
            "forbidden_path_conflicts": ["/tmp/blocked/marker"],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "forbidden path")
}

test_parsed_git_shell_alias_config_forms_are_denied if {
    every test_case in [
        {
            "command": "git config --global alias.pwn !touch",
            "argv": ["git", "config", "--global", "alias.pwn", "!touch"],
        },
        {
            "command": "git --config=alias.pwn=!touch pwn",
            "argv": ["git", "--config=alias.pwn=!touch", "pwn"],
        },
    ] {
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
}

test_parsed_read_only_sed_filename_is_allowed if {
    command := "sed -n '1,10p' w /tmp/other-input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "1,10p", "w", "/tmp/other-input"],
                "error": "",
            }],
        },
    )

    decision.allow == true
}

test_sed_escaped_address_file_write_is_denied if {
    command := "sed -n '\\%foo%w /tmp/marker' input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "\\%foo%w /tmp/marker", "input"],
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_sed_spaced_address_file_write_is_denied if {
    command := "sed -n '1 w /tmp/marker' input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "1 w /tmp/marker", "input"],
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_sed_negated_address_file_write_is_denied if {
    command := "sed -n '1!w /tmp/marker' input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "1!w /tmp/marker", "input"],
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_residual_command_execution_forms_are_denied if {
    every test_case in [
        {
            "command": "git config set core.editor touch",
            "argv": ["git", "config", "set", "core.editor", "touch"],
        },
        {
            "command": "git config --type string core.editor touch",
            "argv": ["git", "config", "--type", "string", "core.editor", "touch"],
        },
        {
            "command": "git config --value foo core.editor touch",
            "argv": ["git", "config", "--value", "foo", "core.editor", "touch"],
        },
        {
            "command": "git config --default foo core.editor touch",
            "argv": ["git", "config", "--default", "foo", "core.editor", "touch"],
        },
        {
            "command": "git config set --value foo core.editor touch",
            "argv": ["git", "config", "set", "--value", "foo", "core.editor", "touch"],
        },
        {
            "command": "cp --target-directory=/repo/.git source",
            "argv": ["cp", "--target-directory=/repo/.git", "source"],
        },
        {
            "command": "cp -t/repo/.git source",
            "argv": ["cp", "-t/repo/.git", "source"],
        },
        {
            "command": "sed --i input",
            "argv": ["sed", "--i", "input"],
        },
        {
            "command": "sed --in input",
            "argv": ["sed", "--in", "input"],
        },
        {
            "command": "sed --inp input",
            "argv": ["sed", "--inp", "input"],
        },
        {
            "command": "tar xvPf archive.tar",
            "argv": ["tar", "xvPf", "archive.tar"],
        },
        {
            "command": "tar --extr archive.tar",
            "argv": ["tar", "--extr", "archive.tar"],
        },
        {
            "command": "git clone -u touch https://example.invalid/repo",
            "argv": ["git", "clone", "-u", "touch", "https://example.invalid/repo"],
        },
        {
            "command": "git clone -u /tmp/git-upload-pack-helper https://example.invalid/repo",
            "argv": ["git", "clone", "-u", "/tmp/git-upload-pack-helper", "https://example.invalid/repo"],
        },
        {
            "command": "git fetch --upload-pack=touch origin",
            "argv": ["git", "fetch", "--upload-pack=touch", "origin"],
        },
        {
            "command": "git ls-remote --upload-pack=touch origin",
            "argv": ["git", "ls-remote", "--upload-pack=touch", "origin"],
        },
        {
            "command": "git difftool --no-prompt -x 'touch /tmp/marker' HEAD^ HEAD",
            "argv": ["git", "difftool", "--no-prompt", "-x", "touch /tmp/marker", "HEAD^", "HEAD"],
        },
        {
            "command": "git rebase -x 'touch /tmp/marker' HEAD^",
            "argv": ["git", "rebase", "-x", "touch /tmp/marker", "HEAD^"],
        },
        {
            "command": "git filter-branch --tree-filter 'touch /tmp/marker' -- --all",
            "argv": ["git", "filter-branch", "--tree-filter", "touch /tmp/marker", "--", "--all"],
        },
        {
            "command": "git -c 'difftool.pwn.cmd=touch /tmp/marker' difftool --tool=pwn HEAD^ HEAD",
            "argv": ["git", "-c", "difftool.pwn.cmd=touch /tmp/marker", "difftool", "--tool=pwn", "HEAD^", "HEAD"],
        },
        {
            "command": "git config mergetool.pwn.cmd 'touch /tmp/marker'",
            "argv": ["git", "config", "mergetool.pwn.cmd", "touch /tmp/marker"],
        },
    ] {
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
}

test_tar_attached_archive_path_conflict_is_denied if {
    command := "tar -cf/tmp/secret/archive.tar input"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["tar", "-cf/tmp/secret/archive.tar", "input"],
                "error": "",
            }],
            "forbidden_path_conflicts": ["/tmp/secret/archive.tar"],
        },
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "forbidden path")
}

test_safe_git_short_u_controls_are_allowed if {
    every test_case in [
        {
            "command": "git add -u",
            "argv": ["git", "add", "-u"],
        },
        {
            "command": "git status -uall",
            "argv": ["git", "status", "-uall"],
        },
        {
            "command": "git -C /tmp/repo add -u",
            "argv": ["git", "-C", "/tmp/repo", "add", "-u"],
        },
        {
            "command": "git --work-tree=/tmp/repo status -uall",
            "argv": ["git", "--work-tree=/tmp/repo", "status", "-uall"],
        },
    ] {
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

        decision.allow == true
    }
}

test_dangerous_git_key_after_option_argument_is_denied if {
    every test_case in [
        {
            "command": "git config --file /tmp/config core.editor touch",
            "argv": ["git", "config", "--file", "/tmp/config", "core.editor", "touch"],
        },
        {
            "command": "git config -f /tmp/config core.editor touch",
            "argv": ["git", "config", "-f", "/tmp/config", "core.editor", "touch"],
        },
        {
            "command": "git config --blob HEAD:config core.editor touch",
            "argv": ["git", "config", "--blob", "HEAD:config", "core.editor", "touch"],
        },
        {
            "command": "git config --type string core.editor touch",
            "argv": ["git", "config", "--type", "string", "core.editor", "touch"],
        },
        {
            "command": "git config --value foo core.editor touch",
            "argv": ["git", "config", "--value", "foo", "core.editor", "touch"],
        },
        {
            "command": "git config --default foo core.editor touch",
            "argv": ["git", "config", "--default", "foo", "core.editor", "touch"],
        },
        {
            "command": "git config set --file /tmp/config core.editor touch",
            "argv": ["git", "config", "set", "--file", "/tmp/config", "core.editor", "touch"],
        },
        {
            "command": "git config set --type string core.editor touch",
            "argv": ["git", "config", "set", "--type", "string", "core.editor", "touch"],
        },
        {
            "command": "git config set --value foo core.editor touch",
            "argv": ["git", "config", "set", "--value", "foo", "core.editor", "touch"],
        },
        {
            "command": "git config set -t path core.editor touch",
            "argv": ["git", "config", "set", "-t", "path", "core.editor", "touch"],
        },
        {
            "command": "git config set --t path core.editor touch",
            "argv": ["git", "config", "set", "--t", "path", "core.editor", "touch"],
        },
        {
            "command": "git config set --ty path core.editor touch",
            "argv": ["git", "config", "set", "--ty", "path", "core.editor", "touch"],
        },
        {
            "command": "git config set --comment note core.editor touch",
            "argv": ["git", "config", "set", "--comment", "note", "core.editor", "touch"],
        },
    ] {
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
}

test_git_push_transport_helper_overrides_are_denied if {
    every command in [
        "git push --receive-pack touch origin HEAD:refs/heads/main",
        "git push --receive-pack=touch origin HEAD:refs/heads/main",
        "git push --receiv=touch origin HEAD:refs/heads/main",
        "git push --exec touch origin HEAD:refs/heads/main",
        "git push --exec=touch origin HEAD:refs/heads/main",
        "git push --ex=touch origin HEAD:refs/heads/main",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_git_send_pack_transport_helpers_are_denied if {
    every test_case in [
        {
            "command": "git send-pack --receive-pack='touch /tmp/marker' origin HEAD:main",
            "argv": [
                "git",
                "send-pack",
                "--receive-pack=touch /tmp/marker",
                "origin",
                "HEAD:main",
            ],
        },
        {
            "command": "git send-pack --exec='touch /tmp/marker' origin HEAD:main",
            "argv": [
                "git",
                "send-pack",
                "--exec=touch /tmp/marker",
                "origin",
                "HEAD:main",
            ],
        },
    ] {
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
        contains(lower(violation), "transport")
    }
}

test_zip_test_command_override_is_denied if {
    every test_case in [
        {
            "command": "zip -q -T -TT touch archive.zip input.txt",
            "argv": ["zip", "-q", "-T", "-TT", "touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip -q -T -TT=touch archive.zip input.txt",
            "argv": ["zip", "-q", "-T", "-TT=touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip -q -T -TTtouch archive.zip input.txt",
            "argv": ["zip", "-q", "-T", "-TTtouch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip --test-command=touch archive.zip input.txt",
            "argv": ["zip", "--test-command=touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip --test-c=touch archive.zip input.txt",
            "argv": ["zip", "--test-c=touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip --test-command touch archive.zip input.txt",
            "argv": ["zip", "--test-command", "touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip --test-c touch archive.zip input.txt",
            "argv": ["zip", "--test-c", "touch", "archive.zip", "input.txt"],
        },
        {
            "command": "zip -qTTtouch archive.zip input.txt",
            "argv": ["zip", "-qTTtouch", "archive.zip", "input.txt"],
        },
    ] {
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
}

test_zip_shell_valued_test_options_are_denied if {
    every test_case in [
        {
            "command": "zip -q -T --test-command='touch /tmp/marker' archive.zip input.txt",
            "argv": [
                "zip",
                "-q",
                "-T",
                "--test-command=touch /tmp/marker",
                "archive.zip",
                "input.txt",
            ],
        },
        {
            "command": "zip -q -T -TT'touch /tmp/marker' archive.zip input.txt",
            "argv": [
                "zip",
                "-q",
                "-T",
                "-TTtouch /tmp/marker",
                "archive.zip",
                "input.txt",
            ],
        },
    ] {
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
        contains(lower(violation), "command")
    }
}

test_tool_output_paths_targeting_git_control_files_are_denied if {
    every test_case in [
        {
            "command": "git diff -o.git/config",
            "argv": ["git", "diff", "-o.git/config"],
        },
        {
            "command": "go build -o=.git/hooks/pre-commit ./cmd",
            "argv": ["go", "build", "-o=.git/hooks/pre-commit", "./cmd"],
        },
        {
            "command": "pytest --basetemp=.git/pytest-tmp",
            "argv": ["pytest", "--basetemp=.git/pytest-tmp"],
        },
        {
            "command": "pytest --junitxml=.git/results.xml",
            "argv": ["pytest", "--junitxml=.git/results.xml"],
        },
        {
            "command": "pytest --log-file=.git/test.log",
            "argv": ["pytest", "--log-file=.git/test.log"],
        },
        {
            "command": "pytest --debug=.git/debug.log",
            "argv": ["pytest", "--debug=.git/debug.log"],
        },
        {
            "command": "uv run pytest --junitxml=.git/results.xml",
            "argv": ["uv", "run", "pytest", "--junitxml=.git/results.xml"],
        },
        {
            "command": "cp -t=.git source",
            "argv": ["cp", "-t=.git", "source"],
        },
        {
            "command": "tar -C.git -tf archive.tar",
            "argv": ["tar", "-C.git", "-tf", "archive.tar"],
        },
        {
            "command": "npm --prefix=.git install --offline",
            "argv": ["npm", "--prefix=.git", "install", "--offline"],
        },
    ] {
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
}

test_executable_git_config_key_families_are_denied if {
    every command in [
        "git config filter.pwn.clean touch",
        "git config filter.pwn.smudge touch",
        "git config filter.pwn.process touch",
        "git config diff.pwn.textconv touch",
        "git config diff.external touch",
        "git config merge.pwn.driver touch",
        "git config gpg.program touch",
        "git config sequence.editor touch",
        "git config includeIf.pwn.path /tmp/include",
        "git config core.askPass touch",
        "git config difftool.pwn.cmd touch",
        "git config mergetool.pwn.cmd touch",
        "git -c credential.https://example.com.helper=!printf username=pwn credential fill",
        "git config credential.https://example.com.helper !touch",
        "git config diff.pwn.command touch",
        "git config core.alternateRefsCommand touch",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_git_config_set_and_option_forms_cannot_set_executable_keys if {
    every test_case in [
        {
            "command": "git config set filter.pwn.clean touch",
            "argv": ["git", "config", "set", "filter.pwn.clean", "touch"],
        },
        {
            "command": "git config set diff.external touch",
            "argv": ["git", "config", "set", "diff.external", "touch"],
        },
        {
            "command": "git config set merge.pwn.driver touch",
            "argv": ["git", "config", "set", "merge.pwn.driver", "touch"],
        },
        {
            "command": "git config set core.askPass touch",
            "argv": ["git", "config", "set", "core.askPass", "touch"],
        },
        {
            "command": "git config set gpg.program touch",
            "argv": ["git", "config", "set", "gpg.program", "touch"],
        },
        {
            "command": "git config set includeIf.pwn.path /tmp/include",
            "argv": [
                "git",
                "config",
                "set",
                "includeIf.pwn.path",
                "/tmp/include",
            ],
        },
        {
            "command": "git config --type string filter.pwn.clean touch",
            "argv": [
                "git",
                "config",
                "--type",
                "string",
                "filter.pwn.clean",
                "touch",
            ],
        },
        {
            "command": "git config --value touch core.askPass touch",
            "argv": [
                "git",
                "config",
                "--value",
                "touch",
                "core.askPass",
                "touch",
            ],
        },
    ] {
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
        contains(lower(violation), "config")
    }
}

test_safe_zip_positional_archive_names_are_allowed if {
    every test_case in [
        {
            "command": "zip -q -T battery.zip input.txt",
            "argv": ["zip", "-q", "-T", "battery.zip", "input.txt"],
        },
        {
            "command": "zip -q -T matter.zip input.txt",
            "argv": ["zip", "-q", "-T", "matter.zip", "input.txt"],
        },
    ] {
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

        decision.allow == true
    }
}

test_old_style_tar_helper_clusters_are_denied if {
    every command in [
        "tar vI touch -c -f archive.tar input.txt",
        "tar vF touch -c -f archive.tar input.txt",
        "tar vIP touch -c -f archive.tar input.txt",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_git_push_force_flags_are_denied_by_profile if {
    every command in [
        "git push --force origin HEAD:refs/heads/main",
        "git push --force-with-lease origin HEAD:refs/heads/main",
        "git push --mirror origin",
        "git push --mir origin",
        "git push -f origin HEAD:refs/heads/main",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_git_push_verification_flags_cannot_bypass_declared_force_push_false if {
    every test_case in [
        {
            "command": "git push --force origin HEAD:refs/heads/main",
            "argv": ["git", "push", "--force", "origin", "HEAD:refs/heads/main"],
        },
        {
            "command": "git push --force-with-lease origin HEAD:refs/heads/main",
            "argv": [
                "git",
                "push",
                "--force-with-lease",
                "origin",
                "HEAD:refs/heads/main",
            ],
        },
        {
            "command": "git push --mirror origin",
            "argv": ["git", "push", "--mirror", "origin"],
        },
        {
            "command": "git push -f origin HEAD:refs/heads/main",
            "argv": ["git", "push", "-f", "origin", "HEAD:refs/heads/main"],
        },
        {
            "command": "git push --delete origin main",
            "argv": ["git", "push", "--delete", "origin", "main"],
        },
        {
            "command": "git push -d origin main",
            "argv": ["git", "push", "-d", "origin", "main"],
        },
        {
            "command": "git push origin +main:main",
            "argv": ["git", "push", "origin", "+main:main"],
        },
        {
            "command": "git push origin :main",
            "argv": ["git", "push", "origin", ":main"],
        },
    ] {
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
        contains(lower(violation), "force push")
    }
}

test_parsed_argv_must_match_raw_command if {
    command := "git apply --unsafe-paths patch"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["echo", "safe"],
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_git_push_destructive_refs_are_denied_by_profile if {
    every command in [
        "git push origin +main:main",
        "git push origin :main",
        "git push --delete origin main",
        "git push -d origin main",
    ] {
        decision := data.governance.approve with input as object.union(
            _base_input,
            {"commands": [command]},
        )

        decision.allow == false
    }
}

test_sed_alternate_delimiter_write_is_denied if {
    command := "sed -n 's@foo@bar@w/tmp/x' input.txt"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {
            "commands": [command],
            "parsed_commands": [{
                "raw": command,
                "argv": ["sed", "-n", "s@foo@bar@w/tmp/x", "input.txt"],
                "error": "",
            }],
        },
    )

    decision.allow == false
}

test_sed_safe_text_containing_w_remains_allowed if {
    every test_case in [
        {
            "command": "sed -n 's/foo/w bar/g' input.txt",
            "argv": ["sed", "-n", "s/foo/w bar/g", "input.txt"],
        },
        {
            "command": "sed -n '/w foo/p' input.txt",
            "argv": ["sed", "-n", "/w foo/p", "input.txt"],
        },
    ] {
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

        decision.allow == true
    }
}

test_sed_multi_segment_write_path_is_denied if {
    every test_case in [
        {
            "command": "sed 's/foo/bar/w /tmp/data/dump.bin' input.txt",
            "argv": ["sed", "s/foo/bar/w /tmp/data/dump.bin", "input.txt"],
        },
        {
            "command": "sed 's/foo/bar/W /var/lib/out.dat' input.txt",
            "argv": ["sed", "s/foo/bar/W /var/lib/out.dat", "input.txt"],
        },
        {
            "command": "sed -n 's@foo@bar@w/tmp/x' input.txt",
            "argv": ["sed", "-n", "s@foo@bar@w/tmp/x", "input.txt"],
        },
    ] {
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
        contains(lower(violation), "sed file")
    }
}

test_sed_address_prefixed_substitution_flags_are_denied if {
    every test_case in [
        {
            "command": "sed '/skip/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
            "argv": ["sed", "/skip/s/foo/bar/w /tmp/a/b/out.bin", "file.txt"],
        },
        {
            "command": "sed '/season/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
            "argv": ["sed", "/season/s/foo/bar/w /tmp/a/b/out.bin", "file.txt"],
        },
        {
            "command": "sed '/skip/s/foo/bar/W /tmp/a/b/out.bin' file.txt",
            "argv": ["sed", "/skip/s/foo/bar/W /tmp/a/b/out.bin", "file.txt"],
        },
        {
            "command": "sed '/skip/s/foo/bar/ep' file.txt",
            "argv": ["sed", "/skip/s/foo/bar/ep", "file.txt"],
        },
        {
            "command": "sed '/skip/,/end/s/foo/bar/w /tmp/a/b/out.bin' file.txt",
            "argv": ["sed", "/skip/,/end/s/foo/bar/w /tmp/a/b/out.bin", "file.txt"],
        },
    ] {
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
        contains(lower(violation), "sed")
    }
}

test_sed_direct_two_address_range_file_io_is_denied if {
    every test_case in [
        {
            "command": "sed '/start/,/end/w /tmp/a/b/out.bin' file.txt",
            "argv": ["sed", "/start/,/end/w /tmp/a/b/out.bin", "file.txt"],
        },
        {
            "command": "sed '10,/end/w /tmp/out.bin' file.txt",
            "argv": ["sed", "10,/end/w /tmp/out.bin", "file.txt"],
        },
        {
            "command": "sed '0,/end/w /tmp/out.bin' file.txt",
            "argv": ["sed", "0,/end/w /tmp/out.bin", "file.txt"],
        },
        {
            "command": "sed '/start/,/end/r /etc/passwd' file.txt",
            "argv": ["sed", "/start/,/end/r /etc/passwd", "file.txt"],
        },
        {
            "command": "sed '/start/,/end/W /tmp/out.bin' file.txt",
            "argv": ["sed", "/start/,/end/W /tmp/out.bin", "file.txt"],
        },
        {
            "command": "sed '/start/,/end/!w /tmp/out.bin' file.txt",
            "argv": ["sed", "/start/,/end/!w /tmp/out.bin", "file.txt"],
        },
    ] {
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
        contains(lower(violation), "sed file")
    }
}

test_sed_numeric_address_with_later_digit_is_denied if {
    every test_case in [
        {
            "command": "sed '3r /tmp/2024/out.bin' file.txt",
            "argv": ["sed", "3r /tmp/2024/out.bin", "file.txt"],
        },
        {
            "command": "sed '3w /tmp/2024/out.bin' file.txt",
            "argv": ["sed", "3w /tmp/2024/out.bin", "file.txt"],
        },
        {
            "command": "sed '3s/foo/bar/w /tmp/2024/out.bin' file.txt",
            "argv": ["sed", "3s/foo/bar/w /tmp/2024/out.bin", "file.txt"],
        },
        {
            "command": "sed '1w /tmp/file1.txt' file.txt",
            "argv": ["sed", "1w /tmp/file1.txt", "file.txt"],
        },
        {
            "command": "sed '3,5w /tmp/2024/out.bin' file.txt",
            "argv": ["sed", "3,5w /tmp/2024/out.bin", "file.txt"],
        },
    ] {
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
        contains(lower(violation), "sed file")
    }
}

test_sed_forward_range_address_file_io_is_denied if {
    every test_case in [
        {
            "command": "sed '/start/,+5w /tmp/out.bin' file.txt",
            "argv": ["sed", "/start/,+5w /tmp/out.bin", "file.txt"],
        },
        {
            "command": "sed '0,+0w /tmp/out.bin' file.txt",
            "argv": ["sed", "0,+0w /tmp/out.bin", "file.txt"],
        },
        {
            "command": "sed '/start/,+5r /etc/passwd' file.txt",
            "argv": ["sed", "/start/,+5r /etc/passwd", "file.txt"],
        },
        {
            "command": "sed '/start/,+5s/foo/bar/w /tmp/out.bin' file.txt",
            "argv": ["sed", "/start/,+5s/foo/bar/w /tmp/out.bin", "file.txt"],
        },
    ] {
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
        contains(lower(violation), "sed file")
    }
}

test_incomplete_input_documents_are_denied if {
    every document in [
        {},
        {"commands": []},
        {"has_objective": true, "has_acceptance": true},
    ] {
        decision := data.governance.approve with input as document

        decision.allow == false
        count(decision.violations) > 0
    }
}

test_commands_require_matching_parsed_argv_records if {
    command := "git p\"ush\" origin +main:main"
    decision := data.governance.approve with input as object.union(
        _base_input,
        {"commands": [command]},
    )

    decision.allow == false
    some violation in decision.violations
    contains(lower(violation), "parsed_commands")
}
