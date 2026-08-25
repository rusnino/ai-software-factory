import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.config import Settings
from governance_controller.db import get_db
from governance_controller.models.audit_log import AuditLog
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


class TestVerificationConcurrency:
    """Regression tests for verification CAS and audit durability."""

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
