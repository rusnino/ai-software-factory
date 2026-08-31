package governance.approve_test

import rego.v1

_base_input := {
    "proposed_by": "agent-1",
    "approval_type": "execution",
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
