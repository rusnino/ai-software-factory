import asyncio
import os
import subprocess
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.client import (
    MacroAgentResponseError,
)
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.config import Settings
from governance_controller.constants import TaskState
from governance_controller.db import get_db
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas import (
    Check,
    CompletionContract,
    ForbiddenPathCheck,
    ScopeCheck,
)
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.state_machine import StateMachine
from governance_controller.services.verification_service import VerificationService


async def test_run_check_times_out_and_records_failed_result(tmp_path) -> None:
    """GAP-095 regression: a long-running check is killed when timeout hits."""
    from governance_controller.schemas.completion_contract import Check

    check = Check(
        type="sleep",
        command="sleep 10",
        expect_exit=0,
    )

    start = asyncio.get_event_loop().time()
    result = await VerificationService._run_check(check, timeout=0.1)
    elapsed = asyncio.get_event_loop().time() - start

    assert result["status"] == "failed"
    assert result["actual_exit"] == -1
    assert result["detail"] == "check timed out"
    assert result["command"] == "sleep 10"
    assert result["expected_exit"] == 0
    # The fix must kill the whole process group, not just the shell. With
    # start_new_session=True + os.killpg(), a forked sleep child dies within
    # a small multiple of the timeout, not after the full 10 seconds.
    assert elapsed < 2.0


async def test_run_check_handles_subprocess_creation_error() -> None:
    """#132: OSError during subprocess creation returns a failed check result."""
    from governance_controller.schemas.completion_contract import Check

    # Use a cwd path that is extremely unlikely to exist so create_subprocess_shell
    # raises OSError before the shell can even be spawned.
    check = Check(
        type="true",
        command="true",
        expect_exit=0,
    )

    result = await VerificationService._run_check(
        check,
        cwd="/this/path/should/not/exist/for/gc/test",
    )

    assert result["status"] == "failed"
    assert result["actual_exit"] == -1
    assert "subprocess creation failed" in result["detail"]
    assert result["command"] == check.command


async def test_run_check_uses_provided_cwd(tmp_path) -> None:
    """Check commands run in the supplied working directory."""
    from governance_controller.schemas.completion_contract import Check

    subdir = tmp_path / "workspace"
    subdir.mkdir()
    (subdir / "marker.txt").write_text("hi")

    check = Check(type="cwd", command="cat marker.txt", expect_exit=0)

    result = await VerificationService._run_check(check, cwd=str(subdir))

    assert result["status"] == "passed"


async def test_run_check_filters_environment(tmp_path, monkeypatch) -> None:
    """Controller secrets are not inherited by verification subprocesses."""
    import os

    from governance_controller.schemas.completion_contract import Check

    monkeypatch.setenv("GC_DATABASE_URL", "postgresql+asyncpg://secret")
    monkeypatch.setenv("GC_MACRO_AGENT_TOKEN", "super-secret")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    check = Check(
        type="env",
        command="env > captured_env.txt && ! grep -q GC_ captured_env.txt",
        expect_exit=0,
    )

    result = await VerificationService._run_check(check, cwd=str(tmp_path))

    assert result["status"] == "passed"


async def test_run_check_uses_settings_timeout_by_default() -> None:
    """_run_check defaults to settings.macro_agent_timeout_seconds."""
    from governance_controller.schemas.completion_contract import Check

    original_settings = Settings()
    with patch(
        "governance_controller.services.verification_service.settings",
        original_settings,
    ):
        # Use a tiny explicit timeout to keep the test fast.
        check = Check(
            type="sleep",
            command="sleep 10",
            expect_exit=0,
        )

        start = asyncio.get_event_loop().time()
        result = await VerificationService._run_check(check, timeout=0.05)
        elapsed = asyncio.get_event_loop().time() - start

    assert result["status"] == "failed"
    assert result["actual_exit"] == -1
    assert elapsed < 2.0


async def test_default_contract_passes_all_checks() -> None:
    contract = TaskContract(
        task_id="task-001",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something",
        acceptance=["It works"],
    )

    result = await VerificationService.verify_execution(contract)

    assert result == {
        "contract_id": "task-001",
        "passed": True,
        "checks": [
            {"name": "forbidden_paths", "status": "passed"},
        ],
    }


async def test_forbidden_path_fails_verification() -> None:
    contract = TaskContract(
        task_id="task-002",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something bad",
        acceptance=["It works"],
        forbidden_paths=[".env", "README.md"],
        inputs=[".env"],
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-002"
    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert ".env" in forbidden["detail"]


async def test_completion_contract_executes_required_checks() -> None:
    contract = TaskContract(
        task_id="task-003",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something with completion contract",
        acceptance=["It works"],
        inputs=["/etc/passwd"],
        completion_contract=CompletionContract(
            task_id="task-003",
            required=[
                Check(type="true", command="true"),
                Check(type="false", command="false", expect_exit=1),
            ],
            forbidden_path_check=ForbiddenPathCheck(paths=["/etc/passwd"]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/", "/etc/"],
                forbidden_paths=["tests/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-003"
    assert result["passed"] is False
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["required:true"]["status"] == "passed"
    assert checks["required:false"]["status"] == "passed"
    assert checks["forbidden_paths"]["status"] == "failed"
    assert checks["scope"]["status"] == "passed"


async def test_completion_contract_required_failure_fails_verification() -> None:
    contract = TaskContract(
        task_id="task-003b",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something with a failing required check",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-003b",
            required=[Check(type="false", command="false", expect_exit=0)],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    check = next(c for c in result["checks"] if c["name"] == "required:false")
    assert check["status"] == "failed"
    assert check["actual_exit"] == 1
    assert check["expected_exit"] == 0


async def test_forbidden_path_prefix_match_fails() -> None:
    contract = TaskContract(
        task_id="task-020",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Touch a subpath of a forbidden directory",
        acceptance=["It is caught"],
        inputs=["~/.ssh/id_rsa"],
        completion_contract=CompletionContract(
            task_id="task-020",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=["~/.ssh"]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert "~/.ssh/id_rsa" in forbidden["detail"]


async def test_task_forbidden_paths_enforced_with_completion_contract() -> None:
    # GAP-074: task-level forbidden_paths must be checked even when a
    # CompletionContract is present and its forbidden_path_check is clear.
    contract = TaskContract(
        task_id="task-023",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Touch a path forbidden only by the task contract",
        acceptance=["It is caught"],
        forbidden_paths=["secrets.env"],
        inputs=["secrets.env"],
        completion_contract=CompletionContract(
            task_id="task-023",
            required=[Check(type="true", command="true")],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert "secrets.env" in forbidden["detail"]


async def test_traversal_forbidden_path_is_rejected() -> None:
    contract = TaskContract(
        task_id="task-021",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Bypass forbidden path check with traversal",
        acceptance=["It is caught"],
        inputs=["src/../../srv/production/secrets.env"],
        forbidden_paths=["/srv/production"],
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert "src/../../srv/production/secrets.env" in forbidden["detail"]


async def test_traversal_scope_conflict_is_rejected() -> None:
    contract = TaskContract(
        task_id="task-022",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Bypass scope check with traversal",
        acceptance=["It is caught"],
        inputs=["src/../../srv/production/secrets.env"],
        completion_contract=CompletionContract(
            task_id="task-022",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/"],
                forbidden_paths=["src/../../srv/production"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    scope_check = next(c for c in result["checks"] if c["name"] == "scope")
    assert scope_check["status"] == "failed"
    assert "src/../../srv/production/secrets.env" in scope_check["detail"]


async def test_completion_contract_scope_conflict_fails() -> None:
    contract = TaskContract(
        task_id="task-004",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something out of scope",
        acceptance=["It works"],
        deliverables=["tests/test_x.py"],
        completion_contract=CompletionContract(
            task_id="task-004",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    scope_check = next(c for c in result["checks"] if c["name"] == "scope")
    assert scope_check["status"] == "failed"
    # tests/ is both forbidden and outside allowed_paths; forbidden wins.
    assert "tests/test_x.py" in scope_check["detail"]


async def test_allowed_paths_outside_scope_fails() -> None:
    """A deliverable outside the allowed_paths allowlist fails the scope check."""
    contract = TaskContract(
        task_id="task-024",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Deliver outside allowed scope",
        acceptance=["It works"],
        deliverables=["docs/leak.md", "src/foo.py"],
        completion_contract=CompletionContract(
            task_id="task-024",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="only src/ and tests/ allowed",
                allowed_paths=["src/", "tests/"],
                forbidden_paths=["pyproject.toml", ".github/workflows/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    scope_check = next(c for c in result["checks"] if c["name"] == "scope")
    assert scope_check["status"] == "failed"
    assert "docs/leak.md" in scope_check["detail"]
    assert "src/foo.py" not in scope_check["detail"]


async def test_optional_check_failure_does_not_fail_verification() -> None:
    contract = TaskContract(
        task_id="task-005",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something optional",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-005",
            required=[],
            optional=[Check(type="false", command="false", expect_exit=0)],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is True
    optional = next(c for c in result["checks"] if c["name"] == "optional:false")
    assert optional["status"] == "failed"


async def test_verification_commands_run_without_completion_contract() -> None:
    contract = TaskContract(
        task_id="task-006",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Run contract-level verification commands",
        acceptance=["It works"],
        verification={"commands": ["true", "true"]},
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-006"
    assert result["passed"] is True
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["required:contract_verification_0"]["status"] == "passed"
    assert checks["required:contract_verification_1"]["status"] == "passed"
    assert checks["forbidden_paths"]["status"] == "passed"


async def test_verification_commands_fail_without_completion_contract() -> None:
    contract = TaskContract(
        task_id="task-007",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Run failing contract-level verification commands",
        acceptance=["It works"],
        verification={"commands": ["true", "false"]},
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-007"
    assert result["passed"] is False
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["required:contract_verification_0"]["status"] == "passed"
    failed = checks["required:contract_verification_1"]
    assert failed["status"] == "failed"
    assert failed["actual_exit"] == 1
    assert failed["expected_exit"] == 0


async def test_forbidden_path_in_command_text_is_detected() -> None:
    """#134: path-like argv tokens in commands are checked against forbidden paths."""
    contract = TaskContract(
        task_id="task-cmd-path",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Exfiltrate via command",
        acceptance=["It works"],
        forbidden_paths=["/tmp/gcpoc_secret_dir"],
        completion_contract=CompletionContract(
            task_id="task-cmd-path",
            required=[
                Check(
                    type="leak",
                    command="cp /tmp/gcpoc_secret_dir/id_rsa /tmp/gcpoc_exfil",
                    expect_exit=0,
                )
            ],
            forbidden_path_check=ForbiddenPathCheck(paths=["/tmp/gcpoc_secret_dir"]),
            scope_check=ScopeCheck(
                description="scope",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert any("/tmp/gcpoc_secret_dir" in str(p) for p in forbidden["detail"])


async def test_tar_directory_path_in_command_is_detected() -> None:
    """#134: verification checks option-glued tar paths too."""
    forbidden = "/tmp/gcpoc_tar_forbidden"
    contract = TaskContract(
        task_id="task-tar-directory-path",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Reject a forbidden tar directory",
        acceptance=["The forbidden path is reported"],
        forbidden_paths=[forbidden],
        completion_contract=CompletionContract(
            task_id="task-tar-directory-path",
            required=[
                Check(
                    type="tar",
                    command=f"tar --directory={forbidden} --list -f /dev/null",
                )
            ],
            forbidden_path_check=ForbiddenPathCheck(paths=[forbidden]),
            scope_check=ScopeCheck(description="tar directory path"),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden_check = next(
        check for check in result["checks"] if check["name"] == "forbidden_paths"
    )
    assert forbidden_check["status"] == "failed"
    assert any(forbidden in str(path) for path in forbidden_check["detail"])


async def test_forbidden_command_path_is_rejected_before_subprocess(tmp_path) -> None:
    """#134: a forbidden command path must not be touched before reporting failure."""
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    marker = forbidden / "marker.txt"
    contract = TaskContract(
        task_id="task-command-preflight",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Reject a forbidden command path before execution",
        acceptance=["The marker is not created"],
        forbidden_paths=[str(forbidden)],
        completion_contract=CompletionContract(
            task_id="task-command-preflight",
            required=[Check(type="touch", command=f"touch {marker}")],
            forbidden_path_check=ForbiddenPathCheck(paths=[str(forbidden)]),
            scope_check=ScopeCheck(description="command preflight"),
        ),
    )

    result = await VerificationService.verify_execution(contract, cwd=str(tmp_path))

    assert result["passed"] is False
    assert marker.exists() is False
    assert all(check["name"] != "required:touch" for check in result["checks"])


async def test_sed_file_io_path_is_rejected_before_subprocess(tmp_path) -> None:
    """#319: a path embedded in a sed script is preflighted before execution."""
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    marker = forbidden / "marker.txt"
    input_path = tmp_path / "input.txt"
    input_path.write_text("payload\n")
    contract = TaskContract(
        task_id="task-sed-preflight",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Reject sed file output before execution",
        acceptance=["The marker is not created"],
        forbidden_paths=[str(forbidden)],
        completion_contract=CompletionContract(
            task_id="task-sed-preflight",
            required=[
                Check(
                    type="sed",
                    command=f"sed -n '1w {forbidden}/marker.txt' {input_path}",
                )
            ],
            forbidden_path_check=ForbiddenPathCheck(paths=[str(forbidden)]),
            scope_check=ScopeCheck(description="sed preflight"),
        ),
    )

    result = await VerificationService.verify_execution(contract, cwd=str(tmp_path))

    assert result["passed"] is False
    assert marker.exists() is False


def _init_git_repo(repo_dir) -> None:
    """Initialize a minimal git repo with one commit, for git-inspection tests."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True, env=env)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=repo_dir, check=True, env=env
    )
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True, env=env
    )


async def test_scope_and_forbidden_checks_detect_actual_git_modified_forbidden_path(
    tmp_path,
) -> None:
    """#406: verify_execution must inspect the real worktree, not just
    self-declared inputs/deliverables and command-text heuristics.

    Reproduces SPEC-03 §3.7's own worked example: `.github/workflows/` is
    forbidden, but the only `required` check is generic (`true`) and never
    mentions it, and it is never declared as an input/deliverable either.
    Before the fix, a forbidden-path modification like this was invisible to
    verify_execution and both `forbidden_paths`/`scope` reported "passed"
    despite the forbidden file having actually changed on disk.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hello')\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    ci_path = repo / ".github" / "workflows" / "ci.yml"
    ci_path.write_text("name: CI\n")
    _init_git_repo(repo)

    # Simulate a completed agent execution that modified a forbidden file
    # never declared as an input/deliverable and never mentioned in any
    # check command's literal text.
    ci_path.write_text("name: CI\non: [push]\n")

    contract = TaskContract(
        task_id="task-406",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Modify src/app.py only",
        acceptance=["It works"],
        inputs=["src/"],
        deliverables=["src/app.py"],
        completion_contract=CompletionContract(
            task_id="task-406",
            required=[Check(type="tests", command="true")],
            forbidden_path_check=ForbiddenPathCheck(paths=[".github/workflows/"]),
            scope_check=ScopeCheck(
                description="SPEC-03 worked example",
                allowed_paths=["src/", "tests/"],
                forbidden_paths=["pyproject.toml", ".github/workflows/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract, cwd=str(repo))

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    scope = next(c for c in result["checks"] if c["name"] == "scope")
    assert forbidden["status"] == "failed"
    assert scope["status"] == "failed"
    assert any(".github/workflows" in path for path in forbidden["detail"])
    assert any(".github/workflows" in path for path in scope["detail"])


async def test_scope_check_passes_when_git_modifications_stay_in_scope(
    tmp_path,
) -> None:
    """Git-based touched-path detection must not flag in-scope changes."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hello')\n")
    _init_git_repo(repo)

    (repo / "src" / "app.py").write_text("print('hello world')\n")

    contract = TaskContract(
        task_id="task-406-ok",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Modify src/app.py only",
        acceptance=["It works"],
        inputs=["src/"],
        deliverables=["src/app.py"],
        completion_contract=CompletionContract(
            task_id="task-406-ok",
            required=[Check(type="tests", command="true")],
            forbidden_path_check=ForbiddenPathCheck(paths=[".github/workflows/"]),
            scope_check=ScopeCheck(
                description="SPEC-03 worked example",
                allowed_paths=["src/", "tests/"],
                forbidden_paths=["pyproject.toml", ".github/workflows/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract, cwd=str(repo))

    assert result["passed"] is True
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["forbidden_paths"]["status"] == "passed"
    assert checks["scope"]["status"] == "passed"


async def test_git_inspection_failure_fails_closed(tmp_path) -> None:
    """A worktree that cannot be git-inspected must not be treated as unchanged."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    contract = TaskContract(
        task_id="task-406-noinspect",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Uninspectable worktree",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-406-noinspect",
            required=[Check(type="tests", command="true")],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(description="n/a"),
        ),
    )

    result = await VerificationService.verify_execution(
        contract, cwd=str(not_a_repo)
    )

    assert result["passed"] is False
    inspection = next(
        c for c in result["checks"] if c["name"] == "git_worktree_inspection"
    )
    assert inspection["status"] == "failed"


async def test_verification_commands_merge_with_completion_contract() -> None:
    contract = TaskContract(
        task_id="task-008",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Merge contract and completion verification commands",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-008",
            required=[Check(type="true", command="true")],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
        verification={"commands": ["false", "true"]},
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-008"
    assert result["passed"] is False
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["required:contract_verification_0"]["status"] == "failed"
    assert checks["required:contract_verification_1"]["status"] == "passed"
    assert checks["required:true"]["status"] == "passed"
    assert checks["forbidden_paths"]["status"] == "passed"
    assert checks["scope"]["status"] == "passed"


async def test_verify_and_advance_logs_cwd_fallback_when_worktree_missing(
    db_session: AsyncSession,
) -> None:
    """#128: falling back to the Controller cwd must leave an audit trail."""
    from governance_controller.constants import TaskState
    from governance_controller.schemas.project_profile import ProjectProfile

    task = Task(
        id="task-fallback-audit",
        project_id="proj-1",
        state=TaskState.AGENT_REVIEW,
        proposed_by="agent-1",
        task_contract_json=TaskContract(
            task_id="task-fallback-audit",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Verify cwd fallback audit",
            acceptance=["audit event present"],
        ).model_dump(mode="json"),
    )
    db_session.add(task)
    await db_session.commit()

    profile = ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/nonexistent/repo/path"},
        execution={"allowed_harnesses": ["opencode"]},
        security={"forbidden_paths": []},
        git={"merge_requires_human": True},
    )
    contract = TaskContract(
        task_id="task-fallback-audit",
        project_id="proj-1",
        proposed_by="agent-1",
        objective="Verify cwd fallback audit",
        acceptance=["audit event present"],
    )

    await VerificationService.verify_and_advance(
        db_session, task, contract, profile=profile
    )

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.task_id == "task-fallback-audit")
            )
        )
        .scalars()
        .all()
    )
    events = [a.event_type for a in audits]
    assert "verification_cwd_fallback" in events
    fallback = next(a for a in audits if a.event_type == "verification_cwd_fallback")
    assert fallback.payload["reason"] == "guessed worktree directory does not exist"
    assert "/nonexistent/repo/path" in fallback.payload["expected_worktree"]


async def test_verify_and_advance_sends_macro_agent_feedback_on_retry(
    db_session: AsyncSession,
) -> None:
    """#191: failed verification pushes feedback to the macro-agent run."""
    from unittest.mock import AsyncMock

    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState

    task = Task(
        id="task-feedback-1",
        project_id="proj-1",
        state=TaskState.AGENT_REVIEW,
        proposed_by="agent-1",
        execution_attempts=0,
        latest_macro_agent_run_id="run-123",
        task_contract_json=TaskContract(
            task_id="task-feedback-1",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Feedback test",
            acceptance=["send feedback"],
            execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
            verification={"commands": ["false"]},
        ).model_dump(mode="json"),
    )
    db_session.add(task)
    await db_session.commit()

    contract = TaskContract(**task.task_contract_json)
    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"run_id": "run-124"})
    fake_executor.feedback = AsyncMock(return_value={"status": "ok"})

    await VerificationService.verify_and_advance(
        db_session, task, contract, executor=fake_executor
    )

    refreshed = await db_session.scalar(
        select(Task).where(Task.id == "task-feedback-1")
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.RUNNING
    fake_executor.feedback.assert_awaited_once()
    call_args = fake_executor.feedback.call_args
    assert call_args is not None
    # Feedback is sent to the *new* retry run (run-124), not the original.
    assert call_args.args[0] == "run-124"
    assert call_args.args[1]["controller_task_id"] == "task-feedback-1"
    assert call_args.args[1]["verification_report"]["passed"] is False


async def test_malformed_retry_start_response_is_audited_and_fails_task(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#285: an invalid retry response must not leave a RUNNING task stranded."""
    from unittest.mock import AsyncMock

    from governance_controller import config
    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    _engine, session_local = isolated_db
    task_id = "task-malformed-retry"
    contract = TaskContract(
        task_id=task_id,
        project_id="project-malformed-retry",
        proposed_by="agent-1",
        objective="Test malformed retry response",
        acceptance=["the retry failure is durable"],
        execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
        verification={"commands": ["false"]},
    )
    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                state=TaskState.AGENT_REVIEW,
                proposed_by="agent-1",
                latest_macro_agent_run_id="run-malformed-original",
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        seed.add(
            Execution(
                id="exec-malformed-retry-original",
                task_id=task_id,
                state=TaskState.RUNNING,
                macro_agent_run_id="run-malformed-original",
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"status": "queued"})
    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        with pytest.raises(RuntimeError, match="retry macro-agent start failed"):
            await VerificationService.verify_and_advance(
                db, task, contract, executor=fake_executor
            )

    async with session_local() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        assert task.state == TaskState.FAILED

        execution_result = await check.execute(
            select(Execution).where(Execution.task_id == task_id)
        )
        executions = execution_result.scalars().all()
        retry = next(
            row
            for row in executions
            if row.id != "exec-malformed-retry-original"
        )
        assert retry.state == TaskState.FAILED
        assert retry.ended_at is not None

        audits = await check.execute(
            select(AuditLog).where(AuditLog.task_id == task_id)
        )
        assert any(
            row.event_type == "retry_execution_start_failed"
            for row in audits.scalars().all()
        )


async def test_external_retry_start_failure_is_terminal_for_pending_attempt(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed external retry start must not reopen the same retry marker."""
    from governance_controller.models.project_profile import ProjectProfileModel
    from governance_controller.schemas.project_profile import ProjectProfile
    from governance_controller.services.stuck_execution_poller import (
        StuckExecutionPoller,
    )

    monkeypatch.setattr(
        StuckExecutionPoller,
        "_retry_recovery_backoff",
        timedelta(0),
        raising=False,
    )
    _engine, session_local = isolated_db
    task_id = "task-retry-start-terminal"
    project_id = "project-retry-start-terminal"
    marker_id = "event-retry-start-terminal"
    contract = TaskContract(
        task_id=task_id,
        project_id=project_id,
        proposed_by="agent-1",
        objective="Do not reopen a failed retry start",
        acceptance=["one retry attempt is terminal after start failure"],
        execution={"timeout_minutes": 1, "max_retries": 2},
    )
    profile = ProjectProfile(
        project_id=project_id,
        repository={"path": "/tmp/retry-start-terminal"},
        execution={"allowed_harnesses": ["opencode"]},
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                state=TaskState.FAILED,
                proposed_by=contract.proposed_by,
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        seed.add(
            ProjectProfileModel(
                project_id=project_id,
                profile_json=profile.model_dump(mode="json"),
            )
        )
        seed.add(
            AuditLog(
                event_id=marker_id,
                event_type="verification_retry_pending",
                task_id=task_id,
                actor="system",
                source="verification_service",
                timestamp=datetime.now(UTC) - timedelta(hours=2),
                payload={
                    "attempt": 1,
                    "max_retries": 2,
                    "verification_report": {"passed": False},
                },
            )
        )
        await seed.commit()

    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.side_effect = RuntimeError("macro-agent unavailable")

    async with session_local() as first:
        first_actions = await StuckExecutionPoller(
            first,
            executor=executor,
        ).poll()
    assert any(
        action["action"] == "verification_retry_recovery_failed"
        for action in first_actions
    )

    async with session_local() as second:
        second_actions = await StuckExecutionPoller(
            second,
            executor=executor,
        ).poll()
        task = await second.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        executions = (
            await second.execute(select(Execution).where(Execution.task_id == task_id))
        ).scalars().all()
        audits = (
            await second.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        ).scalars().all()

    assert not any(
        action["action"] == "verification_retry_recovered"
        for action in second_actions
    )
    executor.start.assert_awaited_once()
    assert task.state == TaskState.FAILED
    assert task.execution_attempts == 1
    assert len(executions) == 1
    assert executions[0].state == TaskState.FAILED
    assert executions[0].ended_at is not None
    assert any(
        row.event_type == "retry_execution_start_failed" for row in audits
    )
    terminal = [
        row
        for row in audits
        if row.event_type == "verification_retry_failed"
        and row.payload.get("pending_event_id") == marker_id
    ]
    assert len(terminal) == 1


async def test_retry_start_rejects_terminal_macro_agent_status(
    isolated_db,
) -> None:
    """#349: verification retry must not treat a terminal dedup hit as fresh."""
    from governance_controller.schemas.project_profile import ProjectProfile

    _engine, session_local = isolated_db
    task_id = "task-retry-terminal-349"
    project_id = "project-retry-terminal-349"

    contract = TaskContract(
        task_id=task_id,
        project_id=project_id,
        proposed_by="agent-1",
        objective="Retry receives terminal dedup response",
        acceptance=["retry treats terminal status as failure"],
        execution={"timeout_minutes": 1, "max_retries": 2},
    )
    profile = ProjectProfile(
        project_id=project_id,
        repository={"path": "/tmp/retry-terminal-349"},
        execution={"allowed_harnesses": ["opencode"]},
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                state=TaskState.RUNNING,
                proposed_by=contract.proposed_by,
                task_contract_json=contract.model_dump(mode="json"),
                execution_attempts=1,
                latest_macro_agent_run_id="sentinel",
            )
        )
        await seed.commit()

    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.return_value = {"run_id": "run-terminal-349", "status": "done"}

    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        with pytest.raises(RuntimeError, match="terminal status"):
            await VerificationService(executor=executor)._start_retry_execution(
                db=db,
                task=task,
                contract=contract,
                profile=profile,
                report={"passed": False},
            )
        await db.commit()

    async with session_local() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        assert task.state == TaskState.FAILED

        execution = await check.scalar(
            select(Execution).where(Execution.task_id == task_id)
        )
        assert execution is not None
        assert execution.state == TaskState.FAILED.value
        assert execution.macro_agent_run_id == "run-terminal-349"

        audit = await check.execute(
            select(AuditLog).where(
                AuditLog.task_id == task_id,
                AuditLog.event_type == "retry_execution_start_failed",
            )
        )
        entries = audit.scalars().all()
        assert len(entries) == 1
        assert entries[0].payload.get("failure_class") == "accepted_response_invalid"


async def test_retry_start_reuses_idempotency_key_after_orphan_loss(
    isolated_db,
) -> None:
    """#301: verification retry reuses the prior execution id as dedup key."""
    from governance_controller.schemas.project_profile import ProjectProfile

    _engine, session_local = isolated_db
    task_id = "task-retry-idempotency-301"
    project_id = "project-retry-idempotency-301"
    prior_key = "exec-orphan-301"

    contract = TaskContract(
        task_id=task_id,
        project_id=project_id,
        proposed_by="agent-1",
        objective="Recover orphan macro-agent run",
        acceptance=["retry uses same controller_execution_id"],
        execution={"timeout_minutes": 1, "max_retries": 2},
    )
    profile = ProjectProfile(
        project_id=project_id,
        repository={"path": "/tmp/retry-idempotency-301"},
        execution={"allowed_harnesses": ["opencode"]},
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                state=TaskState.RUNNING,
                proposed_by=contract.proposed_by,
                task_contract_json=contract.model_dump(mode="json"),
                execution_attempts=1,
                latest_macro_agent_run_id="sentinel",
                macro_agent_idempotency_key=prior_key,
            )
        )
        await seed.commit()

    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.return_value = {"run_id": "run-recovered-301"}
    executor.lookup.return_value = None

    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        result = await VerificationService(executor=executor)._start_retry_execution(
            db=db,
            task=task,
            contract=contract,
            profile=profile,
            report={"passed": False},
        )
        assert result is True
        await db.commit()

    passed_key = executor.start.call_args.kwargs.get(
        "controller_execution_id"
    ) or executor.start.call_args.args[1]
    assert passed_key == prior_key

    async with session_local() as check:
        execution = await check.scalar(
            select(Execution).where(Execution.task_id == task_id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-recovered-301"


async def test_retry_start_recovers_orphaned_run_by_lookup(isolated_db) -> None:
    """#301: a lost retry-start response recovers the existing run by lookup."""
    import httpx

    from governance_controller.schemas.project_profile import ProjectProfile

    _engine, session_local = isolated_db
    task_id = "task-retry-lookup-301"
    project_id = "project-retry-lookup-301"
    prior_key = "exec-orphan-lookup-301"

    contract = TaskContract(
        task_id=task_id,
        project_id=project_id,
        proposed_by="agent-1",
        objective="Recover orphan macro-agent run via lookup",
        acceptance=["retry looks up existing run before failing"],
        execution={"timeout_minutes": 1, "max_retries": 2},
    )
    profile = ProjectProfile(
        project_id=project_id,
        repository={"path": "/tmp/retry-lookup-301"},
        execution={"allowed_harnesses": ["opencode"]},
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                state=TaskState.RUNNING,
                proposed_by=contract.proposed_by,
                task_contract_json=contract.model_dump(mode="json"),
                execution_attempts=1,
                latest_macro_agent_run_id="sentinel",
                macro_agent_idempotency_key=prior_key,
            )
        )
        await seed.commit()

    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.start.side_effect = httpx.ReadTimeout(
        "lost response", request=httpx.Request("POST", "http://macro.example/runs")
    )
    executor.lookup.return_value = {
        "run_id": "run-recovered-lookup-301",
        "status": "queued",
    }

    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        result = await VerificationService(executor=executor)._start_retry_execution(
            db=db,
            task=task,
            contract=contract,
            profile=profile,
            report={"passed": False},
        )
        assert result is True
        await db.commit()

    executor.lookup.assert_awaited_once_with(prior_key)

    async with session_local() as check:
        execution = await check.scalar(
            select(Execution).where(Execution.task_id == task_id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == "run-recovered-lookup-301"


async def test_retry_orphan_recovery_cas_loss_marks_live_run_for_cleanup(
    isolated_db,
) -> None:
    """#359: a lookup hit that then loses the RUNNING CAS to a concurrent
    writer must not silently drop the genuinely live run it just found.
    """
    _engine, session_local = isolated_db
    task_id = "task-retry-359"
    execution_id = "exec-retry-359"

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id="project-retry-359",
                state=TaskState.RUNNING,
                proposed_by="agent-1",
                task_contract_json={"execution": {"timeout_minutes": 1}},
                latest_macro_agent_run_id=execution_id,
            )
        )
        seed.add(
            Execution(
                id=execution_id,
                task_id=task_id,
                state=TaskState.RUNNING,
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    executor = AsyncMock(spec=MacroAgentExecutor)
    executor.lookup.return_value = {
        "run_id": "run-retry-cas-loss-359",
        "status": "queued",
    }

    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        execution = await db.scalar(
            select(Execution).where(Execution.id == execution_id)
        )
        assert task is not None
        assert execution is not None
        expected_version = task.version

        # Simulate a concurrent writer (e.g. a cancellation) moving the
        # task's pointer away from this attempt without updating this
        # in-memory `task` object, so the CAS below observes a stale value.
        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                latest_macro_agent_run_id="something-else",
                version=Task.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()

        recovered = await VerificationService(
            executor=executor
        )._try_recover_orphaned_run(
            db, task, execution, "key-retry-cas-loss-359", expected_version
        )
        assert recovered is False

    async with session_local() as check:
        execution = await check.scalar(
            select(Execution).where(Execution.id == execution_id)
        )
        assert execution is not None
        assert execution.cancellation_pending is True
        assert execution.macro_agent_run_id == "run-retry-cas-loss-359"

        audit_rows = (
            (await check.execute(select(AuditLog).where(AuditLog.task_id == task_id)))
            .scalars()
            .all()
        )
        assert any(
            row.event_type == "retry_execution_cancel_pending"
            and row.payload.get("reason") == "orphan_recovery_cas_lost"
            and row.payload.get("macro_agent_run_id") == "run-retry-cas-loss-359"
            for row in audit_rows
        )


async def test_retry_start_failure_classifies_orphan_risk(isolated_db) -> None:
    """#301: retry start failure audit distinguishes never-sent, lost-response, etc."""
    _engine, session_local = isolated_db
    request = httpx.Request("POST", "http://macro.example/runs")
    response = httpx.Response(500, request=request)
    cases = [
        (httpx.ConnectError("no route", request=request), "never_sent"),
        (httpx.ConnectTimeout("timed out", request=request), "never_sent"),
        (httpx.ReadTimeout("timed out", request=request), "orphan_suspected"),
        (httpx.WriteTimeout("timed out", request=request), "orphan_suspected"),
        (httpx.PoolTimeout("no pool", request=request), "orphan_suspected"),
        (
            httpx.HTTPStatusError("bad", request=request, response=response),
            "rejected",
        ),
        (MacroAgentResponseError("invalid json"), "accepted_response_invalid"),
    ]

    for index, (exc, expected_class) in enumerate(cases):
        task_id = f"task-retry-fail-{expected_class}-{index}"
        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Classify retry start failure",
            acceptance=["failure_class is recorded"],
        )
        async with session_local() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    state=TaskState.RUNNING,
                    proposed_by="agent-1",
                    task_contract_json=contract.model_dump(mode="json"),
                )
            )
            await seed.commit()

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(side_effect=exc)

        async with session_local() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(
                RuntimeError, match="retry macro-agent start failed"
            ):
                await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        async with session_local() as check:
            audits = (
                await check.execute(
                    select(AuditLog).where(AuditLog.task_id == task_id)
                )
            ).scalars().all()
            failure_rows = [
                r
                for r in audits
                if r.event_type == "retry_execution_start_failed"
                and r.payload.get("failure_class") == expected_class
            ]
            assert len(failure_rows) == 1, (
                f"missing failure_class={expected_class} for {exc}"
            )


async def test_finalize_current_execution_uses_populate_existing(isolated_db) -> None:
    """#341/#346: _finalize_current_execution reads fresh DB state, not cache."""
    _engine, session_local = isolated_db
    task_id = "task-finalize-existing"
    execution_id = "exec-finalize-existing"

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id="proj-1",
                state=TaskState.RUNNING,
                proposed_by="agent-1",
            )
        )
        seed.add(
            Execution(
                id=execution_id,
                task_id=task_id,
                state=TaskState.RUNNING,
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    # Session A loads the execution into its identity map.
    session_a = session_local()
    stale_execution = await session_a.scalar(
        select(Execution).where(Execution.id == execution_id)
    )
    assert stale_execution is not None
    assert stale_execution.state == TaskState.RUNNING.value

    # Session B flips the row to READY and commits. READY is still inside the
    # active-state filter, so _finalize_current_execution will proceed; the
    # regression-coverage question is whether it reports the TRUE previous
    # state (READY) or session A's stale cached copy (RUNNING).
    async with session_local() as session_b:
        fresh_execution = await session_b.scalar(
            select(Execution).where(Execution.id == execution_id)
        )
        assert fresh_execution is not None
        fresh_execution.state = TaskState.READY.value
        await session_b.commit()

    await VerificationService._finalize_current_execution(
        session_a, task_id, TaskState.FAILED
    )
    await session_a.commit()
    await session_a.close()

    async with session_local() as check:
        audits = await check.execute(
            select(AuditLog).where(
                AuditLog.task_id == task_id,
                AuditLog.event_type == "execution_finalized",
            )
        )
        entries = audits.scalars().all()
        assert len(entries) == 1
        payload = entries[0].payload
        assert payload.get("previous_state") == TaskState.READY.value, (
            "populate_existing must refresh the identity-map object to the "
            "concurrent writer's READY state, not the cached RUNNING state"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#301: a lost macro-agent start response cannot be correlated "
        "without macro-agent idempotency or lookup"
    ),
)
async def test_accepted_retry_start_is_durably_attached_before_process_loss(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose the retry response-loss window before run-ID attachment commits."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    _engine, session_local = isolated_db
    task_id = "task-retry-response-loss"
    accepted_run_id = "run-retry-accepted-before-loss"
    contract = TaskContract(
        task_id=task_id,
        project_id="project-retry-response-loss",
        proposed_by="agent-1",
        objective="Test retry response loss",
        acceptance=["the accepted run is durably attached"],
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                state=TaskState.RUNNING,
                proposed_by="agent-1",
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        await seed.commit()

    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(  # type: ignore[method-assign]
        return_value={"run_id": accepted_run_id}
    )

    async with session_local() as db:
        task = await db.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        original_execute = db.execute
        execute_count = 0

        async def _crash_before_attachment_commit(*args: object, **kwargs: object):
            nonlocal execute_count
            result = await original_execute(*args, **kwargs)
            execute_count += 1
            # The second explicit execute is the post-start task attachment CAS.
            if execute_count == 2:
                raise asyncio.CancelledError
            return result

        db.execute = _crash_before_attachment_commit  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await VerificationService(
                executor=fake_executor
            )._start_retry_execution(
                db=db,
                task=task,
                contract=contract,
                profile=None,
                report={"passed": False},
            )

    async with session_local() as check:
        execution = await check.scalar(
            select(Execution).where(Execution.task_id == task_id)
        )
        assert execution is not None
        assert execution.macro_agent_run_id == accepted_run_id


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_verify_commits_before_slow_plane_alert(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#283: failed state/audit must commit before alerting Plane."""
    from unittest.mock import AsyncMock

    from governance_controller import config
    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    class _SlowAlert:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def notify_verification_failure(self, **_kwargs: object) -> None:
            self.started.set()
            await self.release.wait()

    _engine, session_local = isolated_db
    slow_alert = _SlowAlert()
    monkeypatch.setattr(config.settings, "plane_base_url", "")
    monkeypatch.setattr(
        "governance_controller.services.verification_service.AlertService",
        lambda: slow_alert,
    )

    task_id = "task-alert-boundary-283"
    contract = TaskContract(
        task_id=task_id,
        project_id="project-alert-283",
        proposed_by="agent-1",
        objective="Test alert transaction boundary",
        acceptance=["failure state is durable before alerting"],
        execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
        verification={"commands": ["false"]},
    )
    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                state=TaskState.AGENT_REVIEW,
                proposed_by="agent-1",
                latest_macro_agent_run_id="run-alert-original",
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        seed.add(
            Execution(
                id="exec-alert-boundary-283",
                task_id=task_id,
                state=TaskState.RUNNING,
                macro_agent_run_id="run-alert-original",
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"run_id": "run-alert-retry"})
    fake_executor.feedback = AsyncMock(return_value={"status": "ok"})
    verification_task = None
    async with session_local() as worker:
        task = await worker.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        verification_task = asyncio.create_task(
            VerificationService.verify_and_advance(
                worker, task, contract, executor=fake_executor
            )
        )
        try:
            await slow_alert.started.wait()
            async with session_local() as observer:
                task_row = await observer.scalar(select(Task).where(Task.id == task_id))
                failed_audits = await observer.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_id,
                        AuditLog.event_type == "verification_failed",
                    )
                )
                pending_audits = await observer.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_id,
                        AuditLog.event_type == "plane_projection_pending",
                    )
                )

            assert task_row is not None
            assert task_row.state == TaskState.FAILED
            assert failed_audits.scalars().first() is not None
            pending = pending_audits.scalars().first()
            assert pending is not None
            assert pending.payload["operation"] == "verification_failure_alert"
        finally:
            slow_alert.release.set()
            assert verification_task is not None
            await verification_task


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_retry_audit_commits_before_macro_agent_feedback(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#283: retry-start audit must commit before feedback I/O."""
    from unittest.mock import AsyncMock

    from governance_controller import config
    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    class _SlowFeedback:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(
            self, _run_id: str, _payload: dict[str, object]
        ) -> dict[str, object]:
            self.started.set()
            await self.release.wait()
            return {"status": "ok"}

    _engine, session_local = isolated_db
    slow_feedback = _SlowFeedback()
    monkeypatch.setattr(config.settings, "plane_base_url", "")

    task_id = "task-feedback-boundary-283"
    contract = TaskContract(
        task_id=task_id,
        project_id="project-feedback-283",
        proposed_by="agent-1",
        objective="Test feedback transaction boundary",
        acceptance=["retry audit is durable before feedback"],
        execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
        verification={"commands": ["false"]},
    )
    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                state=TaskState.AGENT_REVIEW,
                proposed_by="agent-1",
                latest_macro_agent_run_id="run-feedback-original",
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        seed.add(
            Execution(
                id="exec-feedback-boundary-283",
                task_id=task_id,
                state=TaskState.RUNNING,
                macro_agent_run_id="run-feedback-original",
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"run_id": "run-feedback-retry"})
    fake_executor.feedback = slow_feedback  # type: ignore[method-assign]
    verification_task = None
    async with session_local() as worker:
        task = await worker.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        verification_task = asyncio.create_task(
            VerificationService.verify_and_advance(
                worker, task, contract, executor=fake_executor
            )
        )
        try:
            await slow_feedback.started.wait()
            async with session_local() as observer:
                retry_audits = await observer.execute(
                    select(AuditLog).where(
                        AuditLog.task_id == task_id,
                        AuditLog.event_type == "retry_execution_start",
                    )
                )

            assert retry_audits.scalars().first() is not None
        finally:
            slow_feedback.release.set()
            assert verification_task is not None
            await verification_task


async def test_verify_and_advance_finalizes_execution_to_human_review(
    db_session: AsyncSession,
) -> None:
    """#219: a passing verification finalizes the RUNNING execution row."""
    from unittest.mock import AsyncMock

    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    execution = Execution(
        id="exec-pass-1",
        task_id="task-pass-finalize",
        state=TaskState.RUNNING,
        macro_agent_run_id="run-123",
        started_at=datetime.now(UTC),
    )
    db_session.add(execution)

    task = Task(
        id="task-pass-finalize",
        project_id="proj-1",
        state=TaskState.AGENT_REVIEW,
        proposed_by="agent-1",
        latest_macro_agent_run_id="run-123",
        task_contract_json=TaskContract(
            task_id="task-pass-finalize",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Finalize on pass",
            acceptance=["finalize"],
            execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
            verification={"commands": ["true"]},
        ).model_dump(mode="json"),
    )
    db_session.add(task)
    await db_session.commit()

    contract = TaskContract(**task.task_contract_json)
    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"run_id": "run-124"})

    await VerificationService.verify_and_advance(
        db_session, task, contract, executor=fake_executor
    )

    refreshed = await db_session.scalar(
        select(Execution).where(Execution.id == "exec-pass-1")
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.HUMAN_REVIEW
    assert refreshed.ended_at is not None


async def test_verify_and_advance_projects_state_to_plane(
    db_session: AsyncSession,
) -> None:
    """#156: a passing verification pushes the new state to Plane."""
    from unittest.mock import AsyncMock

    from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    execution = Execution(
        id="exec-plane-projection",
        task_id="task-plane-projection",
        state=TaskState.RUNNING,
        macro_agent_run_id="run-123",
        started_at=datetime.now(UTC),
    )
    db_session.add(execution)

    task = Task(
        id="task-plane-projection",
        project_id="proj-1",
        state=TaskState.AGENT_REVIEW,
        proposed_by="agent-1",
        plane_issue_id="plane-issue-156",
        latest_macro_agent_run_id="run-123",
        task_contract_json=TaskContract(
            task_id="task-plane-projection",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Project to Plane",
            acceptance=["project"],
            execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
            verification={"commands": ["true"]},
        ).model_dump(mode="json"),
    )
    db_session.add(task)
    await db_session.commit()

    contract = TaskContract(**task.task_contract_json)
    fake_executor = MacroAgentExecutor()
    fake_executor.start = AsyncMock(return_value={"run_id": "run-124"})
    fake_projection: Any = AsyncMock()
    fake_projection.update_state.return_value = {"id": "plane-issue-156"}

    await VerificationService.verify_and_advance(
        db_session,
        task,
        contract,
        executor=fake_executor,
        plane_projection=fake_projection,
    )

    fake_projection.update_state.assert_awaited_once_with(
        controller_task_id="task-plane-projection",
        plane_issue_id="plane-issue-156",
        state=TaskState.HUMAN_REVIEW,
        project_id="proj-1",
        opentasks_id=None,
    )


async def test_verify_and_advance_finalizes_execution_to_failed(
    db_session: AsyncSession,
) -> None:
    """#219: a failing verification finalizes the RUNNING execution row."""
    from governance_controller.constants import TaskState
    from governance_controller.models.execution import Execution

    execution = Execution(
        id="exec-fail-1",
        task_id="task-fail-finalize",
        state=TaskState.RUNNING,
        macro_agent_run_id="run-123",
        started_at=datetime.now(UTC),
    )
    db_session.add(execution)

    task = Task(
        id="task-fail-finalize",
        project_id="proj-1",
        state=TaskState.AGENT_REVIEW,
        proposed_by="agent-1",
        execution_attempts=2,
        latest_macro_agent_run_id="run-123",
        task_contract_json=TaskContract(
            task_id="task-fail-finalize",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Finalize on fail",
            acceptance=["finalize"],
            execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
            verification={"commands": ["false"]},
        ).model_dump(mode="json"),
    )
    db_session.add(task)
    await db_session.commit()

    contract = TaskContract(**task.task_contract_json)

    await VerificationService.verify_and_advance(db_session, task, contract)

    refreshed = await db_session.scalar(
        select(Execution).where(Execution.id == "exec-fail-1")
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.FAILED
    assert refreshed.ended_at is not None


async def test_cancelled_retry_transition_leaves_recoverable_marker(
    isolated_db,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled retry handoff must leave a durable recovery marker."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    _engine, session_local = isolated_db
    task_id = "task-cancelled-retry-transition"
    contract = TaskContract(
        task_id=task_id,
        project_id="proj-cancelled-retry",
        proposed_by="agent-1",
        objective="Recover a cancelled verification retry",
        acceptance=["the retry starts after recovery"],
        execution={"max_retries": 2, "harness": "opencode", "role": "worker"},
        verification={"commands": ["false"]},
    )

    async with session_local() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=contract.project_id,
                state=TaskState.AGENT_REVIEW,
                proposed_by=contract.proposed_by,
                latest_macro_agent_run_id="run-original-cancelled-retry",
                task_contract_json=contract.model_dump(mode="json"),
            )
        )
        seed.add(
            Execution(
                id="exec-original-cancelled-retry",
                task_id=task_id,
                state=TaskState.RUNNING,
                macro_agent_run_id="run-original-cancelled-retry",
                started_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    async def _cancel_retry_transition(
        _db: AsyncSession,
        _task: Task,
        execution_attempts: int,
    ) -> bool:
        del execution_attempts
        raise asyncio.CancelledError

    monkeypatch.setattr(
        StateMachine,
        "atomic_transition_from_failed_to_running",
        staticmethod(_cancel_retry_transition),
    )
    fake_executor = AsyncMock(spec=MacroAgentExecutor)
    service = VerificationService(executor=fake_executor)

    with pytest.raises(asyncio.CancelledError):
        async with asynccontextmanager(get_db)() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            await service.verify_and_advance(db, task, contract)

    async with session_local() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        assert task.state == TaskState.FAILED
        audits = (
            await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        ).scalars().all()
        assert any(row.event_type == "verification_retry_pending" for row in audits)


class TestVerificationConcurrency:
    """Regression tests for verification CAS and audit durability."""

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="lock interleaving requires PostgreSQL row locks",
    )
    async def test_retry_start_does_not_deadlock_on_task_execution_lock_interleave(
        self,
        isolated_db: tuple,
    ) -> None:
        """A retry CAS race must not hold Execution while waiting for Task."""
        _engine, local_session = isolated_db
        task_id = "task-retry-lock-interleave"
        run_id = "run-retry-lock-interleave"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise retry lock ordering",
            acceptance=["the retry does not deadlock"],
        )
        cas_about_to_start = asyncio.Event()
        racer_has_task_lock = asyncio.Event()
        racer_task: asyncio.Task[None] | None = None
        racer_error: BaseException | None = None
        start_returned = False

        async def _start(*args: object, **_kwargs: object) -> dict[str, str]:
            nonlocal racer_task, start_returned
            execution_id = str(args[1])

            async def _hold_task_then_wait_for_execution() -> None:
                nonlocal racer_error
                try:
                    async with local_session() as racer:
                        await racer.execute(
                            update(Task)
                            .where(
                                Task.id == task_id,  # type: ignore[arg-type]
                                Task.state == TaskState.RUNNING.value,  # type: ignore[arg-type]
                            )
                            .values(
                                state=TaskState.BLOCKED.value,
                                version=Task.version + 1,
                            )
                        )
                        racer_has_task_lock.set()
                        await cas_about_to_start.wait()
                        await racer.execute(
                            update(Execution)
                            .where(Execution.id == execution_id)  # type: ignore[arg-type]
                            .values(status_error="lock-order-test")
                        )
                        await racer.commit()
                except BaseException as exc:
                    racer_error = exc

            racer_task = asyncio.create_task(_hold_task_then_wait_for_execution())
            await racer_has_task_lock.wait()
            start_returned = True
            return {"run_id": run_id}

        executor = MacroAgentExecutor()
        executor.start = AsyncMock(  # type: ignore[method-assign]
            side_effect=_start
        )

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            original_execute = db.execute

            async def _execute(*args: object, **kwargs: object):
                if start_returned and not cas_about_to_start.is_set():
                    # The next explicit execute is the post-start task CAS.
                    # Let the racer request Execution immediately before that
                    # CAS so an early execution flush creates a real cycle.
                    cas_about_to_start.set()
                return await original_execute(*args, **kwargs)

            db.execute = _execute  # type: ignore[method-assign]
            operation = asyncio.create_task(
                VerificationService(executor=executor)._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )
            )
            try:
                done, _pending = await asyncio.wait({operation}, timeout=2.0)
                if not done:
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
                    pytest.fail(
                        "retry start deadlocked while acquiring Task after Execution"
                    )
                try:
                    await operation
                except ValueError as exc:
                    assert "Concurrent modification detected" in str(exc)
                else:
                    pytest.fail("retry start ignored the concurrent task transition")
            finally:
                if not operation.done():
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)

        if racer_task is not None:
            await asyncio.wait_for(racer_task, timeout=2.0)
        assert racer_error is None, f"racer failed: {racer_error}"

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="poller-first interleave requires PostgreSQL row locks",
    )
    async def test_retry_cleanup_rechecks_pending_claim_after_poller_cleanup(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verification must not cancel or audit after the poller claims cleanup."""
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        _engine, session_local = isolated_db
        task_id = "task-retry-cleanup-poller-race"
        run_id = "run-retry-cleanup-poller-race"
        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Coordinate retry cleanup",
            acceptance=["one cancellation is issued"],
        )

        async with session_local() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id=contract.project_id,
                    proposed_by=contract.proposed_by,
                    state=TaskState.RUNNING,
                    execution_attempts=1,
                )
            )
            await seed.commit()

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with session_local() as racer:
                current = await racer.scalar(select(Task).where(Task.id == task_id))
                assert current is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == current.version,  # type: ignore[arg-type]
                        Task.state == TaskState.RUNNING.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        cancel_sources: list[str] = []

        async def _cancel(source: str) -> dict[str, object]:
            cancel_sources.append(source)
            return {}

        async def _verifier_cancel(_run_id: str) -> dict[str, object]:
            return await _cancel("verifier")

        async def _poller_cancel(_run_id: str) -> dict[str, object]:
            return await _cancel("poller")

        verifier_executor = AsyncMock(spec=MacroAgentExecutor)
        verifier_executor.start.side_effect = _start_then_lose
        verifier_executor.cancel.side_effect = _verifier_cancel
        poller_executor = AsyncMock(spec=MacroAgentExecutor)
        poller_executor.cancel.side_effect = _poller_cancel

        original_transition = StateMachine.atomic_transition

        async def _lose_running_cas(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            if target_state == TaskState.RUNNING:
                return False
            return await original_transition(db, task, target_state)

        monkeypatch.setattr(
            StateMachine,
            "atomic_transition",
            staticmethod(_lose_running_cas),
        )

        pending_committed = asyncio.Event()
        release_cleanup = asyncio.Event()
        commit_count = 0

        async with session_local() as db:
            real_commit = db.commit

            async def _commit() -> None:
                nonlocal commit_count
                await real_commit()
                commit_count += 1
                if commit_count == 2:
                    pending_committed.set()
                    await release_cleanup.wait()

            monkeypatch.setattr(db, "commit", _commit)
            operation = asyncio.create_task(
                VerificationService(
                    executor=verifier_executor
                )._start_retry_execution(
                    db=db,
                    task=await db.scalar(select(Task).where(Task.id == task_id)),
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )
            )
            await asyncio.wait_for(pending_committed.wait(), timeout=5)

            async with session_local() as poller_db:
                actions = await StuckExecutionPoller(
                    poller_db,
                    executor=poller_executor,
                )._poll_pending_cancellations()

            release_cleanup.set()
            with pytest.raises(ValueError, match="Concurrent modification"):
                await asyncio.wait_for(operation, timeout=5)

        assert actions[0]["action"] == "execution_cancel_completed"
        assert cancel_sources == ["poller"]
        verifier_executor.cancel.assert_not_awaited()
        poller_executor.cancel.assert_awaited_once_with(run_id)

        async with session_local() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.latest_macro_agent_run_id == run_id
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.cancellation_pending is False
            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert (
                len(
                    [
                        row
                        for row in audits
                        if row.event_type == "execution_cancel_completed"
                    ]
                )
                == 1
            )

    @pytest.mark.skipif(
        os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="targets SQLite writer-lock behavior",
    )
    async def test_sqlite_retry_cleanup_is_single_flight_with_poller(
        self,
        isolated_db: tuple,
    ) -> None:
        """SQLite must not let the poller duplicate a retry cleanup call."""
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        _engine, local_session = isolated_db
        task_id = "task-sqlite-retry-cleanup-single-flight"
        run_id = "run-sqlite-retry-cleanup-single-flight"
        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Coordinate retry cleanup",
            acceptance=["one cancellation is issued"],
        )

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id=contract.project_id,
                    proposed_by=contract.proposed_by,
                    state=TaskState.RUNNING,
                    execution_attempts=1,
                )
            )
            await seed.commit()

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                current = await racer.scalar(select(Task).where(Task.id == task_id))
                assert current is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == current.version,  # type: ignore[arg-type]
                        Task.state == TaskState.RUNNING.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        verifier_cancel_started = asyncio.Event()
        poller_cancel_started = asyncio.Event()
        release_verifier_cancel = asyncio.Event()
        cancel_sources: list[str] = []

        async def _verifier_cancel(_run_id: str) -> dict[str, object]:
            cancel_sources.append("verifier")
            verifier_cancel_started.set()
            await release_verifier_cancel.wait()
            return {}

        async def _poller_cancel(_run_id: str) -> dict[str, object]:
            cancel_sources.append("poller")
            poller_cancel_started.set()
            return {}

        verifier_executor = AsyncMock(spec=MacroAgentExecutor)
        verifier_executor.start.side_effect = _start_then_lose
        verifier_executor.cancel.side_effect = _verifier_cancel
        poller_executor = AsyncMock(spec=MacroAgentExecutor)
        poller_executor.cancel.side_effect = _poller_cancel

        async with local_session() as verifier_db:
            task = await verifier_db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            verifier_operation = asyncio.create_task(
                VerificationService(
                    executor=verifier_executor
                )._start_retry_execution(
                    db=verifier_db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )
            )
            await asyncio.wait_for(verifier_cancel_started.wait(), timeout=5)

            async with local_session() as poller_db:
                poller_operation = asyncio.create_task(
                    StuckExecutionPoller(
                        poller_db,
                        executor=poller_executor,
                    )._poll_pending_cancellations()
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(poller_cancel_started.wait(), timeout=1)

                release_verifier_cancel.set()
                with pytest.raises(ValueError, match="Concurrent modification"):
                    await asyncio.wait_for(verifier_operation, timeout=5)
                actions = await asyncio.wait_for(poller_operation, timeout=5)

        assert cancel_sources == ["verifier"]
        verifier_executor.cancel.assert_awaited_once_with(run_id)
        poller_executor.cancel.assert_not_awaited()
        assert actions == []

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_concurrent_retry_starts_have_one_external_claim(
        self,
        isolated_db: tuple,
    ) -> None:
        """Two retry starters must not both call macro-agent start."""
        _engine, local_session = isolated_db
        task_id = "task-retry-single-flight"
        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Claim one retry",
            acceptance=["only one external run starts"],
        )

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id=contract.project_id,
                    proposed_by=contract.proposed_by,
                    state=TaskState.RUNNING,
                    execution_attempts=1,
                )
            )
            await seed.commit()

        async def _start(*args: object, **_kwargs: object) -> dict[str, str]:
            await asyncio.sleep(0.05)
            assert len(args) > 1
            return {"run_id": f"run-{args[1]}"}

        executor = AsyncMock(spec=MacroAgentExecutor)
        executor.start.side_effect = _start

        async def _invoke() -> bool:
            async with local_session() as db:
                task = await db.scalar(select(Task).where(Task.id == task_id))
                assert task is not None
                result = await VerificationService(
                    executor=executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )
                await db.commit()
                return result

        results = await asyncio.gather(_invoke(), _invoke(), return_exceptions=True)

        assert results.count(True) == 1
        assert all(result in {True, False} for result in results)
        executor.start.assert_awaited_once()

        async with local_session() as check:
            executions = (
                await check.execute(
                    select(Execution).where(Execution.task_id == task_id)
                )
            ).scalars().all()
            assert len(executions) == 1
            assert executions[0].macro_agent_run_id is not None

    async def test_retry_attachment_increments_task_version(
        self,
        isolated_db,
    ) -> None:
        """A retry run attachment must invalidate stale poller reads."""
        from unittest.mock import AsyncMock

        _engine, session_local = isolated_db
        task_id = "task-retry-version"
        contract = TaskContract(
            task_id=task_id,
            project_id="proj-retry-version",
            proposed_by="agent-1",
            objective="Advance retry version",
            acceptance=["the task version changes with run attachment"],
        )

        async with session_local() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id=contract.project_id,
                    proposed_by=contract.proposed_by,
                    state=TaskState.RUNNING,
                    execution_attempts=1,
                )
            )
            await seed.commit()

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(return_value={"run_id": "run-version"})

        async with session_local() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            await VerificationService(executor=fake_executor)._start_retry_execution(
                db=db,
                task=task,
                contract=contract,
                profile=None,
                report={"passed": False},
            )
            await db.commit()

        async with session_local() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            # The durable single-flight claim and the later external run-ID
            # attachment are separate authoritative task updates.
            assert task.version == 2
            assert task.latest_macro_agent_run_id == "run-version"

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_retry_start_success_cas_loss_does_not_attach_run_to_changed_task(
        self,
        isolated_db: tuple,
    ) -> None:
        """#294: a retry run must not attach after a competing task transition."""
        from unittest.mock import AsyncMock

        from governance_controller.adapters.macro_agent.executor import (
            MacroAgentExecutor,
        )
        from governance_controller.constants import TaskState
        from governance_controller.models.execution import Execution

        _engine, local_session = isolated_db
        task_id = "task-retry-attach-cas-294"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise retry attachment CAS",
            acceptance=["the retry is not attached after a task race"],
        )

        async def _start_then_block_elsewhere(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.BLOCKED
                )
                assert won is True
                await racer.commit()
            return {"run_id": "run-retry-cas-294"}

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(  # type: ignore[method-assign]
            side_effect=_start_then_block_elsewhere
        )
        fake_executor.cancel = AsyncMock(  # type: ignore[method-assign]
            return_value={}
        )

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        fake_executor.cancel.assert_awaited_once_with("run-retry-cas-294")

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.BLOCKED
            assert task.latest_macro_agent_run_id != "run-retry-cas-294"

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.BLOCKED
            assert execution.ended_at is not None
            assert execution.macro_agent_run_id == "run-retry-cas-294"
            assert execution.cancellation_pending is False

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == task_id)
            )
            assert any(
                row.event_type == "retry_execution_start_cas_lost"
                for row in audits.scalars().all()
            )

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_retry_recovery_race_caller_does_not_clobber_concurrent_winner(
        self,
        isolated_db: tuple,
    ) -> None:
        """#362: _start_retry_execution's own CAS-loss path must not re-fail a
        task that a concurrent recovery (e.g. the stuck-execution poller)
        already brought to RUNNING for the exact same run, while this
        caller's own lookup was still in flight.

        Unlike ``test_retry_start_recovers_orphaned_run_by_lookup`` (which
        only exercises a single attempt) or a direct call into
        ``_try_recover_orphaned_run`` (which only exercises the helper),
        this drives ``_start_retry_execution`` itself -- the real caller --
        with a genuine concurrent winner racing in a second Postgres
        session.
        """
        import httpx

        _engine, local_session = isolated_db
        task_id = "task-retry-recovery-race-362"
        prior_key = "exec-retry-recovery-race-362"
        run_id = "run-retry-recovery-race-362"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                    macro_agent_idempotency_key=prior_key,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise retry recovery race",
            acceptance=["a concurrent winner's RUNNING state is not clobbered"],
        )

        lookup_entered = asyncio.Event()
        release_lookup = asyncio.Event()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = httpx.ReadTimeout(
            "lost response", request=httpx.Request("POST", "http://macro.example/runs")
        )

        async def _lookup(_key: str) -> dict[str, str]:
            lookup_entered.set()
            await release_lookup.wait()
            return {"run_id": run_id, "status": "queued"}

        fake_executor.lookup.side_effect = _lookup

        async def _run_retry() -> bool:
            async with local_session() as db:
                task = await db.scalar(select(Task).where(Task.id == task_id))
                assert task is not None
                return await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        retry_task = asyncio.create_task(_run_retry())
        await asyncio.wait_for(lookup_entered.wait(), timeout=5)

        # A concurrent writer (the poller, in production) wins the recovery
        # race first, using the real production helper on its own session.
        async with local_session() as winner_db:
            winner_task = await winner_db.scalar(
                select(Task).where(Task.id == task_id)
            )
            winner_execution = await winner_db.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert winner_task is not None
            assert winner_execution is not None
            expected_version = winner_task.version
            winner_executor = AsyncMock(spec=MacroAgentExecutor)
            winner_executor.lookup.return_value = {
                "run_id": run_id,
                "status": "queued",
            }
            won = await VerificationService(
                executor=winner_executor
            )._try_recover_orphaned_run(
                winner_db,
                winner_task,
                winner_execution,
                prior_key,
                expected_version,
            )
            assert won is True

        release_lookup.set()
        # Pre-fix, this raises RuntimeError("retry macro-agent start failed:
        # ...") because the caller's own CAS-loss path re-reads the winner's
        # refreshed RUNNING state/version and legally CASes it back to
        # FAILED. Post-fix, it returns True cleanly instead.
        result = await asyncio.wait_for(retry_task, timeout=5)
        assert result is True

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.RUNNING
            assert task.latest_macro_agent_run_id == run_id

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.RUNNING
            assert execution.macro_agent_run_id == run_id

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_retry_recovery_race_caller_does_not_clobber_winner_past_running(
        self,
        isolated_db: tuple,
    ) -> None:
        """#364: the benign-race guard must not require the concurrent winner
        to still be sitting in RUNNING. If the winner legitimately advances
        past RUNNING (e.g. to AGENT_REVIEW, via ordinary verification) before
        this caller's own stalled lookup resolves, the CAS loss is still
        benign -- not a genuine orphan -- and must not clobber the winner's
        execution row.

        Unlike ``test_retry_recovery_race_caller_does_not_clobber_concurrent_winner``
        (#362, where the winner is still RUNNING when the loser's CAS loses),
        this drives the winner one step further -- to AGENT_REVIEW -- with a
        real concurrent Postgres session before releasing the loser, so the
        pre-fix guard's ``fresh_task.state == TaskState.RUNNING`` check is
        genuinely false and falls into the clobbering branch.
        """
        _engine, local_session = isolated_db
        task_id = "task-retry-recovery-race-past-running-364"
        prior_key = "exec-retry-recovery-race-past-running-364"
        run_id = "run-retry-recovery-race-past-running-364"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                    macro_agent_idempotency_key=prior_key,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise retry recovery race past RUNNING",
            acceptance=["a winner's AGENT_REVIEW state is not clobbered"],
        )

        lookup_entered = asyncio.Event()
        release_lookup = asyncio.Event()

        fake_executor = AsyncMock(spec=MacroAgentExecutor)
        fake_executor.start.side_effect = httpx.ReadTimeout(
            "lost response", request=httpx.Request("POST", "http://macro.example/runs")
        )

        async def _lookup(_key: str) -> dict[str, str]:
            lookup_entered.set()
            await release_lookup.wait()
            return {"run_id": run_id, "status": "queued"}

        fake_executor.lookup.side_effect = _lookup

        async def _run_retry() -> bool:
            async with local_session() as db:
                task = await db.scalar(select(Task).where(Task.id == task_id))
                assert task is not None
                return await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        retry_task = asyncio.create_task(_run_retry())
        await asyncio.wait_for(lookup_entered.wait(), timeout=5)

        # A concurrent writer (the poller, in production) wins the recovery
        # race first, then ordinary verification legitimately advances the
        # task/execution past RUNNING to AGENT_REVIEW -- all committed before
        # this caller's own stalled lookup resolves.
        async with local_session() as winner_db:
            winner_task = await winner_db.scalar(
                select(Task).where(Task.id == task_id)
            )
            winner_execution = await winner_db.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert winner_task is not None
            assert winner_execution is not None
            expected_version = winner_task.version
            winner_executor = AsyncMock(spec=MacroAgentExecutor)
            winner_executor.lookup.return_value = {
                "run_id": run_id,
                "status": "queued",
            }
            won = await VerificationService(
                executor=winner_executor
            )._try_recover_orphaned_run(
                winner_db,
                winner_task,
                winner_execution,
                prior_key,
                expected_version,
            )
            assert won is True

            # ``_try_recover_orphaned_run`` attaches the run via a raw
            # ``update()`` with ``synchronize_session=False``, which does not
            # refresh ``winner_task`` in place -- re-read it so the version
            # used below matches what was actually committed.
            winner_task = await winner_db.scalar(
                select(Task)
                .where(Task.id == task_id)
                .execution_options(populate_existing=True)
            )
            assert winner_task is not None
            advanced = await StateMachine.atomic_transition(
                winner_db, winner_task, TaskState.AGENT_REVIEW
            )
            assert advanced is True
            winner_execution.state = TaskState.AGENT_REVIEW
            await winner_db.commit()

        release_lookup.set()
        # Pre-fix, this raises RuntimeError("retry macro-agent start failed:
        # ...") because the guard only recognizes the winner as benign while
        # it is still exactly RUNNING; seeing AGENT_REVIEW instead, it falls
        # into the genuine-orphan branch, and the caller's own fallthrough
        # then clobbers with a ValueError. Post-fix, it returns True cleanly
        # instead, leaving the winner's progress untouched.
        result = await asyncio.wait_for(retry_task, timeout=5)
        assert result is True

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.AGENT_REVIEW
            assert task.latest_macro_agent_run_id == run_id

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.AGENT_REVIEW
            assert execution.macro_agent_run_id == run_id
            assert execution.cancellation_pending is False
            assert execution.ended_at is None

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_retry_start_cas_loss_crash_persists_cancel_intent_atomically(
        self,
        isolated_db: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#293: a crash cannot separate finalization from cancellation intent."""
        _engine, local_session = isolated_db
        task_id = "task-retry-cas-loss-crash"
        run_id = "run-retry-cas-loss-crash"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise atomic retry cleanup intent",
            acceptance=["cleanup intent survives a crash"],
        )

        async def _start_then_lose(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.BLOCKED
                )
                assert won is True
                await racer.commit()
            return {"run_id": run_id}

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(side_effect=_start_then_lose)  # type: ignore[method-assign]
        fake_executor.cancel = AsyncMock(return_value={})  # type: ignore[method-assign]

        async with local_session() as db:
            real_commit = db.commit
            commit_count = 0

            async def _commit_then_crash() -> None:
                nonlocal commit_count
                await real_commit()
                commit_count += 1
                # The pre-fix second commit contains only finalization and its
                # audit, leaving cancellation recovery unmarked.
                if commit_count == 2:
                    raise asyncio.CancelledError

            monkeypatch.setattr(db, "commit", _commit_then_crash)
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(asyncio.CancelledError):
                await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )
            assert commit_count == 2

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.BLOCKED

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.BLOCKED
            assert execution.ended_at is not None
            assert execution.macro_agent_run_id == run_id
            assert execution.cancellation_pending is True

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "retry_execution_start_cas_lost" for row in audits
            )
            assert any(
                row.event_type == "execution_cancel_pending"
                and row.payload["macro_agent_run_id"] == run_id
                for row in audits
            )
        fake_executor.cancel.assert_not_awaited()

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_retry_start_matched_run_on_failed_task_is_cleaned_up(
        self,
        isolated_db: tuple,
    ) -> None:
        """A matching run pointer does not make a terminal task owned by retry."""
        _engine, local_session = isolated_db
        task_id = "task-retry-matched-failed"
        run_id = "run-retry-matched-failed"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise matched run cleanup",
            acceptance=["terminal task owns no active retry"],
        )

        async def _start_then_fail_elsewhere(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == racing_task.version,  # type: ignore[arg-type]
                        Task.state == TaskState.RUNNING.value,  # type: ignore[arg-type]
                    )
                    .values(
                        state=TaskState.FAILED.value,
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(  # type: ignore[method-assign]
            side_effect=_start_then_fail_elsewhere
        )
        fake_executor.cancel = AsyncMock(return_value={})  # type: ignore[method-assign]

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await VerificationService(
                    executor=fake_executor
                )._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        fake_executor.cancel.assert_awaited_once_with(run_id)

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            assert task.state == TaskState.FAILED
            assert task.latest_macro_agent_run_id == run_id

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.FAILED
            assert execution.ended_at is not None
            assert execution.macro_agent_run_id == run_id
            assert execution.cancellation_pending is False

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "retry_execution_start_cas_lost" for row in audits
            )
            assert any(
                row.event_type == "execution_cancel_pending"
                and row.payload["macro_agent_run_id"] == run_id
                for row in audits
            )

    async def test_cas_loss_commits_audit_before_raise(
        self,
        isolated_db: tuple,
        patched_db,
    ) -> None:
        """A failed verify_and_advance CAS still persists its audit row.

        The production code's atomic_transition returns False on a stale read.
        Without an explicit commit, the concurrent_modification audit log row
        would vanish when ``get_db()`` rolls back the containing transaction on
        the propagated ValueError. This test drives the real ``get_db()`` path
        to prove the audit row survives.
        """
        from governance_controller.constants import TaskState

        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-verify-cas",
                project_id="proj-1",
                state=TaskState.AGENT_REVIEW,
                proposed_by="agent-1",
                task_contract_json=TaskContract(
                    task_id="task-verify-cas",
                    project_id="proj-1",
                    proposed_by="agent-1",
                    objective="Verify CAS audit persists",
                    acceptance=["audit survives"],
                ).model_dump(mode="json"),
            )
            seed.add(task)
            await seed.commit()

        # Force the CAS to lose as if another request already advanced the task.
        async def _patched(
            db: AsyncSession, task: Task, target_state: TaskState
        ) -> bool:
            return False

        with patch.object(StateMachine, "atomic_transition", staticmethod(_patched)):
            contract = TaskContract(
                task_id="task-verify-cas",
                project_id="proj-1",
                proposed_by="agent-1",
                objective="Verify CAS audit persists",
                acceptance=["audit survives"],
            )

            with pytest.raises(ValueError, match="Concurrent modification detected"):
                async with asynccontextmanager(get_db)() as db:
                    task = await db.scalar(
                        select(Task).where(Task.id == "task-verify-cas")
                    )
                    assert task is not None
                    await VerificationService.verify_and_advance(db, task, contract)

        async with local_session() as check:
            task = await check.scalar(select(Task).where(Task.id == "task-verify-cas"))
            assert task is not None
            assert task.state == TaskState.AGENT_REVIEW
            assert task.version == 0

            audits = await check.execute(
                select(AuditLog).where(AuditLog.task_id == "task-verify-cas")
            )
            events = {a.event_type for a in audits.scalars().all()}
            assert "concurrent_modification" in events

    async def test_start_retry_execution_failed_cas_loss_does_not_stomp_execution(
        self,
        isolated_db: tuple,
    ) -> None:
        """#266/#303/#305: a lost task CAS finalizes only the local execution.

        ``_start_retry_execution`` commits the new RUNNING ``Execution`` row and
        releases the task row lock before the outbound ``executor.start()``
        call. If that call raises and a concurrent session has already moved the
        task elsewhere, the task CAS loses; the local Execution is finalized as
        FAILED without copying the competing task state. No external run ID was
        returned, so no cancellation is queued.
        """
        from unittest.mock import AsyncMock

        from governance_controller.adapters.macro_agent.executor import (
            MacroAgentExecutor,
        )
        from governance_controller.constants import TaskState
        from governance_controller.models.execution import Execution
        from governance_controller.services.state_machine import StateMachine

        engine, local_session = isolated_db

        async with local_session() as seed:
            task = Task(
                id="task-retry-race-266",
                project_id="proj-1",
                state=TaskState.RUNNING,
                proposed_by="agent-1",
                execution_attempts=1,
            )
            seed.add(task)
            await seed.commit()

        contract = TaskContract(
            task_id="task-retry-race-266",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="race retry start",
            acceptance=["ok"],
        )

        async def _race_then_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
            # A genuinely separate session/transaction wins a real CAS to
            # BLOCKED while the macro-agent /runs call is "in flight" — only
            # possible because _start_retry_execution already committed and
            # released the task row lock before this call.
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == "task-retry-race-266")
                )
                assert racing_task is not None
                won = await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.BLOCKED
                )
                assert won is True, "setup error: racer CAS should win"
                await racer.commit()
            raise RuntimeError("macro-agent unreachable")

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(side_effect=_race_then_fail)  # type: ignore[method-assign]

        async with local_session() as db:
            task = await db.scalar(
                select(Task).where(Task.id == "task-retry-race-266")
            )
            assert task is not None
            service = VerificationService(executor=fake_executor)

            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await service._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        async with local_session() as check:
            refreshed_task = await check.scalar(
                select(Task).where(Task.id == "task-retry-race-266")
            )
            assert refreshed_task is not None
            # The racer's CAS is the one that genuinely won.
            assert refreshed_task.state == TaskState.BLOCKED

            executions = await check.execute(
                select(Execution).where(
                    Execution.task_id == "task-retry-race-266"
                )
            )
            rows = executions.scalars().all()
            assert len(rows) == 1
            # The local start failed, but the competing winner still owns the
            # task state. Finalize this execution without copying BLOCKED.
            assert rows[0].state == TaskState.FAILED
            assert rows[0].ended_at is not None
            assert rows[0].macro_agent_run_id is None
            assert rows[0].cancellation_pending is False

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="retry-start CAS-loss regression requires PostgreSQL",
    )
    async def test_retry_start_failure_after_task_cas_loss_finalizes_execution(
        self,
        isolated_db: tuple,
    ) -> None:
        """#305: a failed start cannot leave a RUNNING orphan behind."""
        _engine, local_session = isolated_db
        task_id = "task-retry-start-failure-cas-305"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="race retry start failure",
            acceptance=["the local execution is terminal"],
        )

        async def _race_then_fail(*_args: object, **_kwargs: object) -> dict[str, str]:
            async with local_session() as racer:
                racing_task = await racer.scalar(select(Task).where(Task.id == task_id))
                assert racing_task is not None
                assert await StateMachine.atomic_transition(
                    racer, racing_task, TaskState.BLOCKED
                )
                await racer.commit()
            raise RuntimeError("macro-agent unreachable")

        executor = MacroAgentExecutor()
        executor.start = AsyncMock(side_effect=_race_then_fail)  # type: ignore[method-assign]

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None
            with pytest.raises(ValueError, match="Concurrent modification detected"):
                await VerificationService(executor=executor)._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=None,
                    report={"passed": False},
                )

        async with local_session() as check:
            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.FAILED
            assert execution.ended_at is not None

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_matched_run_id_branch_flush_does_not_revert_third_writer(
        self,
        isolated_db: tuple,
    ) -> None:
        """A matched run must be revalidated before the retry reports success.

        When ``cas_result`` (the run-ID attach CAS) loses because a second
        writer B already attached the *exact same* ``macro_agent_run_id``,
        ``_start_retry_execution`` must verify that the current task is still
        RUNNING and owns that pointer before treating the run as attached.

        This test seats a THIRD, independently-CAS'd writer C exactly between
        the first fresh re-read and the ownership check (via a live re-read
        hook, not a mock/theoretical race). Writer C wins a real
        ``StateMachine.atomic_transition`` to FAILED (version N -> N+1) and
        commits. The local execution must be finalized and the external run
        cancelled without reverting C's task transition.
        """
        _engine, local_session = isolated_db
        task_id = "task-retry-matched-branch-race"

        async with local_session() as seed:
            seed.add(
                Task(
                    id=task_id,
                    project_id="proj-1",
                    proposed_by="agent-1",
                    state=TaskState.RUNNING,
                )
            )
            await seed.commit()

        contract = TaskContract(
            task_id=task_id,
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Exercise the CAS-lost matched-run-id branch",
            acceptance=["writer C's committed progress is not reverted"],
        )

        run_id = "run-matched-branch"

        async def _start_then_attach_elsewhere(
            *_args: object, **_kwargs: object
        ) -> dict[str, str]:
            # Writer B: attach the SAME run id our own CAS will try to
            # attach, so our cas_result loses but the fresh re-read
            # "matches" instead of hitting the safe raise path.
            async with local_session() as racer:
                racing_task = await racer.scalar(
                    select(Task).where(Task.id == task_id)
                )
                assert racing_task is not None
                result = await racer.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,  # type: ignore[arg-type]
                        Task.version == racing_task.version,  # type: ignore[arg-type]
                    )
                    .values(
                        latest_macro_agent_run_id=run_id,
                        version=Task.version + 1,
                    )
                )
                assert result.rowcount == 1  # type: ignore[attr-defined]
                await racer.commit()
            return {"run_id": run_id}

        fake_executor = MacroAgentExecutor()
        fake_executor.start = AsyncMock(  # type: ignore[method-assign]
            side_effect=_start_then_attach_elsewhere
        )
        fake_executor.cancel = AsyncMock(return_value={})  # type: ignore[method-assign]

        async with local_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            assert task is not None

            original_execute = db.execute
            call_count = {"n": 0}

            async def patched_execute(*args: object, **kwargs: object):
                result = await original_execute(*args, **kwargs)
                call_count["n"] += 1
                # The 3rd explicit db.execute() call in
                # _start_retry_execution's CAS-lost path is the initial
                # populate_existing fresh-task read. Seat writer C's real,
                # independently-CAS'd commit immediately after that read
                # returns, before the ownership re-read runs.
                if call_count["n"] == 3:
                    async with local_session() as writer_c:
                        current = await writer_c.scalar(
                            select(Task).where(Task.id == task_id)
                        )
                        assert current is not None
                        won = await StateMachine.atomic_transition(
                            writer_c, current, TaskState.FAILED
                        )
                        assert won is True, "setup error: writer C CAS should win"
                        await writer_c.commit()
                return result

            db.execute = patched_execute  # type: ignore[method-assign]
            try:
                with pytest.raises(
                    ValueError, match="Concurrent modification detected"
                ):
                    await VerificationService(
                        executor=fake_executor
                    )._start_retry_execution(
                        db=db,
                        task=task,
                        contract=contract,
                        profile=None,
                        report={"passed": False},
                    )
            finally:
                db.execute = original_execute  # type: ignore[method-assign]
            await db.commit()

        fake_executor.cancel.assert_awaited_once_with(run_id)
        assert call_count["n"] == 8, (
            "setup error: expected exactly 8 db.execute calls "
            "(claim, run-ID CAS, initial fresh read, initial task lock, "
            "cleanup execution lock, cleanup task lock, guarded claim, "
            "claim release)"
        )

        async with local_session() as check:
            final = await check.scalar(select(Task).where(Task.id == task_id))
            assert final is not None
            # Writer C's real transition must survive regardless of A's cleanup.
            assert final.state == TaskState.FAILED
            assert final.version >= 3, (
                "retry cleanup reverted writer C's committed version advance: "
                f"task.version={final.version}, state={final.state}"
            )
            assert final.latest_macro_agent_run_id == run_id

            execution = await check.scalar(
                select(Execution).where(Execution.task_id == task_id)
            )
            assert execution is not None
            assert execution.state == TaskState.FAILED
            assert execution.ended_at is not None
            assert execution.macro_agent_run_id == run_id
            assert execution.cancellation_pending is False

            audits = (
                await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
            ).scalars().all()
            assert any(
                row.event_type == "retry_execution_start_cas_lost" for row in audits
            )
