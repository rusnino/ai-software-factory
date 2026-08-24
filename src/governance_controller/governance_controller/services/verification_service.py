"""Deterministic verification harness for the Governance Controller.

When a :class:`TaskContract` carries a :class:`CompletionContract`, the service
executes the declared required/optional shell commands and compares their exit
codes to ``Check.expect_exit``. It also performs static forbidden-path and scope
checks. If verification passes, the task is advanced from ``AGENT_REVIEW`` to
``HUMAN_REVIEW``; if it fails, the task moves to ``FAILED``.
"""

import asyncio
import contextlib
import os
import signal
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.config import settings
from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.completion_contract import Check
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.alert_service import AlertService
from governance_controller.services.audit_service import AuditService
from governance_controller.services.policy_engine import (
    _extract_command_paths,
    _forbidden_path_conflicts,
)
from governance_controller.services.state_machine import StateMachine
from governance_controller.utils.paths import normalize_path

_logger = structlog.get_logger("governance_controller.verification")


class VerificationService:
    """Execute completion-contract checks and drive the AGENT_REVIEW gate."""

    def __init__(
        self,
        executor: MacroAgentExecutor | None = None,
    ) -> None:
        self.executor = executor or MacroAgentExecutor()

    # Environment variables that are safe to propagate to verification checks.
    # Controller secrets (DB credentials, macro-agent tokens, etc.) are excluded.
    # HOME/USER/SHELL are intentionally omitted: HOME is scoped to the worktree
    # (or omitted), and USER/SHELL leak the Controller process identity without
    # adding value to the check.
    _SAFE_ENV_KEYS: frozenset[str] = frozenset(
        {"PATH", "LANG", "LC_ALL", "TERM", "PWD"}
    )

    @staticmethod
    async def _run_check(
        check: Check,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> dict[str, object]:
        """Run a single Check command and return a result dict.

        Args:
            check: The command to run and expected exit code.
            timeout: Maximum seconds to wait for the subprocess. ``None``
                uses ``settings.macro_agent_timeout_seconds``.
            cwd: Working directory for the subprocess. ``None`` uses the
                Controller process's current working directory.
        """
        if timeout is None:
            timeout = settings.macro_agent_timeout_seconds

        env = {
            key: value
            for key, value in os.environ.items()
            if key in VerificationService._SAFE_ENV_KEYS
        }
        # Scope HOME to the task worktree when one is supplied. This prevents
        # an approved check from reading/writing the Controller user's real home
        # directory via $HOME or ~ expansion. A worktree is not a sandbox, but
        # scoping HOME is a cheap layer of filesystem isolation.
        if cwd is not None:
            env["HOME"] = cwd
        try:
            proc = await asyncio.create_subprocess_shell(
                check.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                cwd=cwd,
                env=env,
            )
        except OSError as exc:
            logger = structlog.get_logger("governance_controller.audit")
            logger.info(
                "audit_log_entry",
                event_type="verification_check_failed",
                task_id="unknown",
                actor="system",
                source="verification_service",
                payload={
                    "command": check.command,
                    "expected_exit": check.expect_exit,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "detail": "subprocess creation failed",
                },
            )
            return {
                "name": f"required:{check.type}",
                "status": "failed",
                "command": check.command,
                "expected_exit": check.expect_exit,
                "actual_exit": -1,
                "stdout": "",
                "stderr": "",
                "detail": f"subprocess creation failed: {exc}",
            }

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            actual_exit = proc.returncode or 0
            status = "passed" if actual_exit == check.expect_exit else "failed"
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                # Kill the whole process group so forked children (e.g. a
                # shell-spawned sleep) cannot outlive the shell itself.
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)

            stdout, stderr = b"", b""
            actual_exit = -1
            status = "failed"

        result: dict[str, object] = {
            "name": f"required:{check.type}",
            "status": status,
            "command": check.command,
            "expected_exit": check.expect_exit,
            "actual_exit": actual_exit,
        }
        if status == "failed":
            result["stdout"] = stdout.decode(errors="replace")
            result["stderr"] = stderr.decode(errors="replace")
            if actual_exit == -1:
                result["detail"] = "check timed out"
        return result

    @staticmethod
    def _verification_commands_from_contract(contract: TaskContract) -> list[Check]:
        """Return extra verification checks declared in ``TaskContract.verification``.

        SPEC-03 §3.5 allows ``verification.commands`` to be a list of shell
        command strings. These are translated into ``Check`` objects and merged
        with the CompletionContract checks so that verification commands from
        the task contract are also executed.
        """
        verification = contract.verification
        if not verification:
            return []

        commands = verification.get("commands", [])
        if not isinstance(commands, list):
            return []

        converted: list[Check] = []
        for idx, command in enumerate(commands):
            if isinstance(command, str):
                converted.append(
                    Check(
                        type=f"contract_verification_{idx}",
                        command=command,
                        expect_exit=0,
                    )
                )
        return converted

    @classmethod
    async def verify_execution(
        cls,
        contract: TaskContract,
        cwd: str | None = None,
    ) -> dict[str, object]:
        """Run all verification checks for *contract* and return a report.

        Verification commands declared in ``TaskContract.verification``
        (SPEC-03 §3.5) are always executed. If a CompletionContract is also
        present, its required/optional commands are executed via
        ``asyncio.create_subprocess_shell`` and their exit codes compared to
        ``Check.expect_exit``. Static forbidden-path and scope checks are
        performed when a CompletionContract is present; otherwise the task
        contract's own ``forbidden_paths`` are checked.

        Args:
            contract: The task contract to verify.
            cwd: Working directory for verification subprocesses. ``None``
                uses the Controller process's current working directory.

        Returns:
            ``{"contract_id": ..., "passed": bool, "checks": [...]}``
        """
        checks: list[dict[str, object]] = []
        passed = True

        completion = contract.completion_contract
        touched_paths = set(contract.inputs + contract.deliverables)

        # Always execute TaskContract.verification.commands (SPEC-03 §3.5),
        # even when there is no CompletionContract.
        contract_verification_checks = cls._verification_commands_from_contract(
            contract
        )
        for check in contract_verification_checks:
            result = await cls._run_check(check, cwd=cwd)
            checks.append(result)
            if result["status"] == "failed":
                passed = False

        if completion is not None:
            for check in completion.required:
                result = await cls._run_check(check, cwd=cwd)
                checks.append(result)
                if result["status"] == "failed":
                    passed = False

            for check in completion.optional:
                result = await cls._run_check(check, cwd=cwd)
                checks.append(
                    {
                        "name": f"optional:{check.type}",
                        "status": result["status"],
                        "command": check.command,
                        "expected_exit": check.expect_exit,
                        "actual_exit": result["actual_exit"],
                    }
                )
                # Optional checks do not fail the overall verification.

        # Forbidden path check: merge task-level forbidden_paths with those from
        # the CompletionContract (when present). Also include path-like tokens
        # extracted from every executed command so a check cannot read or write
        # a forbidden path that was never declared as an input/deliverable.
        forbidden_paths: list[str] = list(contract.forbidden_paths)
        if completion is not None:
            forbidden_paths = list(
                set(forbidden_paths) | set(completion.forbidden_path_check.paths)
            )

        command_paths: set[str] = set()
        for check in contract_verification_checks:
            command_paths |= _extract_command_paths(check.command)
        if completion is not None:
            for check in list(completion.required) + list(completion.optional):
                command_paths |= _extract_command_paths(check.command)
        touched_paths |= command_paths

        forbidden_touches = set(
            _forbidden_path_conflicts(touched_paths, forbidden_paths)
        )
        if forbidden_touches:
            passed = False
            checks.append(
                {
                    "name": "forbidden_paths",
                    "status": "failed",
                    "detail": sorted(forbidden_touches),
                }
            )
        else:
            checks.append({"name": "forbidden_paths", "status": "passed"})

        if completion is not None:
            # Scope check: treat forbidden/allowed paths as prefixes.
            scope = completion.scope_check
            scope_conflicts: set[str] = set()

            allowed_paths = [
                path for path in scope.allowed_paths if path and isinstance(path, str)
            ]
            if allowed_paths:
                for touched in touched_paths:
                    if not any(
                        cls._is_prefixed_by(touched, prefix) for prefix in allowed_paths
                    ):
                        scope_conflicts.add(touched)

            for touched in touched_paths:
                if any(
                    cls._is_prefixed_by(touched, prefix)
                    for prefix in scope.forbidden_paths
                ):
                    scope_conflicts.add(touched)

            if scope_conflicts:
                passed = False
                checks.append(
                    {
                        "name": "scope",
                        "status": "failed",
                        "detail": sorted(scope_conflicts),
                    }
                )
            else:
                checks.append({"name": "scope", "status": "passed"})

        return {
            "contract_id": contract.task_id,
            "passed": passed,
            "checks": checks,
        }

    @staticmethod
    def _is_prefixed_by(path: str, prefix: str) -> bool:
        """Return True if *path* equals *prefix* or is under it."""
        normalized_path = normalize_path(path)
        normalized_prefix = normalize_path(prefix)
        return normalized_path == normalized_prefix or normalized_path.startswith(
            normalized_prefix + "/"
        )

    @classmethod
    async def verify_and_advance(
        cls,
        db: AsyncSession,
        task: Task,
        contract: TaskContract,
        profile: ProjectProfile | None = None,
        executor: MacroAgentExecutor | None = None,
    ) -> dict[str, object]:
        """Verify *contract* and atomically advance *task* out of ``AGENT_REVIEW``.

        The task must already be in ``AGENT_REVIEW``. On success it transitions
        to ``HUMAN_REVIEW``; on failure it transitions to ``FAILED`` unless a
        retry remains, in which case it transitions to ``RUNNING`` and starts a
        new macro-agent execution. The state change uses the same
        compare-and-swap discipline as ``ApprovalService`` so concurrent events
        cannot silently clobber each other.
        """
        service = cls(executor=executor)

        # Determine a task-specific worktree when a repository path is provided.
        # Only use the path if it actually exists; otherwise fall back to no cwd
        # override so a guessed/non-existent path does not crash verification
        # before any state transition is attempted (#118).
        worktree_path: str | None = None
        fallback_to_cwd = False
        if profile is not None:
            repo_path = getattr(profile.repository, "path", None)
            if repo_path:
                candidate = os.path.join(str(repo_path), "worktrees", task.id)
                if os.path.isdir(candidate):
                    worktree_path = candidate
                else:
                    fallback_to_cwd = True

        report = await service.verify_execution(contract, cwd=worktree_path)

        # SPEC-03 §3.8: record when verification silently degrades to the
        # Controller's own cwd because the guessed worktree is missing.
        if fallback_to_cwd:
            await AuditService.log(
                db=db,
                event_type="verification_cwd_fallback",
                task_id=task.id,
                actor="system",
                source="verification_service",
                payload={
                    "expected_worktree": candidate,
                    "reason": "guessed worktree directory does not exist",
                    "verification_report": report,
                },
            )

        if report["passed"]:
            target_state = TaskState.HUMAN_REVIEW
            event_type = "verification_passed"
        else:
            target_state = TaskState.FAILED
            event_type = "verification_failed"

        if not await StateMachine.atomic_transition(db, task, target_state):
            await AuditService.log(
                db=db,
                event_type="concurrent_modification",
                task_id=task.id,
                actor="system",
                source="verification_service",
                payload={
                    "expected_state": TaskState.AGENT_REVIEW.value,
                    "target_state": target_state.value,
                    "verification_report": report,
                },
            )
            # Commit so the audit row survives rollback when the exception
            # propagates out of get_db().
            await db.commit()
            raise ValueError(
                "Concurrent modification detected: "
                "task state changed during verification"
            )

        await AuditService.log(
            db=db,
            event_type=event_type,
            task_id=task.id,
            actor="system",
            source="verification_service",
            payload=report,
        )

        # SPEC-09 §9.6: after failing verification, retry before marking FAILED
        # if retries remain. Each failed verification from AGENT_REVIEW counts as
        # one execution attempt. atomic_transition above moved the task to
        # FAILED, so the scoped FAILED -> RUNNING transition verifies the row is
        # in FAILED and has the just-incremented version.
        if target_state == TaskState.FAILED:
            max_retries = getattr(contract.execution, "max_retries", 2)
            if task.execution_attempts >= max_retries:
                await service._alert_human_terminal_failure(
                    db=db,
                    task=task,
                    contract=contract,
                    report=report,
                    reason="max_retries_exhausted",
                )
                await AlertService().notify_terminal_failure(
                    task=task,
                    contract=contract,
                    report=report,
                    reason="max_retries_exhausted",
                )
                return report
            await AlertService().notify_verification_failure(
                task=task,
                contract=contract,
                report=report,
                attempt=task.execution_attempts,
                max_retries=max_retries,
            )
            if await StateMachine.atomic_transition_from_failed_to_running(
                db,
                task,
                execution_attempts=task.execution_attempts + 1,
            ):
                await AuditService.log(
                    db=db,
                    event_type="verification_failed_retry",
                    task_id=task.id,
                    actor="system",
                    source="verification_service",
                    payload={
                        "execution_attempts": task.execution_attempts,
                        "max_retries": max_retries,
                        "verification_report": report,
                    },
                )
                await service._start_retry_execution(
                    db=db,
                    task=task,
                    contract=contract,
                    profile=profile,
                    report=report,
                )
                # SPEC-09 §9.6 step 2: send failure feedback to macro-agent so
                # the retry run receives the previous verification report.
                await service._send_macro_agent_feedback(
                    task=task,
                    contract=contract,
                    report=report,
                )
            else:
                await AuditService.log(
                    db=db,
                    event_type="concurrent_modification",
                    task_id=task.id,
                    actor="system",
                    source="verification_service",
                    payload={
                        "expected_state": TaskState.FAILED.value,
                        "target_state": TaskState.RUNNING.value,
                        "verification_report": report,
                    },
                )
                await db.commit()
                raise ValueError(
                    "Concurrent modification detected: "
                    "task state changed during verification retry"
                )

        return report

    async def _start_retry_execution(
        self,
        db: AsyncSession,
        task: Task,
        contract: TaskContract,
        profile: ProjectProfile | None,
        report: dict[str, object],
    ) -> None:
        """Start a new macro-agent execution for a verification retry.

        Mirrors ``ApprovalService._trigger_execution`` but skips the READY phase
        because the task is already approved and we are resuming execution.
        """
        started_at = datetime.now(UTC)
        execution = Execution(
            id=str(uuid4()),
            task_id=task.id,
            state=TaskState.RUNNING,
            started_at=started_at,
        )
        db.add(execution)
        await db.flush()

        # Commit before the outbound macro-agent call so no task row lock is held
        # across the potentially slow network request.
        await db.commit()

        try:
            sandbox = profile.execution.sandbox if profile is not None else "worktree"
            max_parallel_agents = (
                profile.execution.max_parallel_agents if profile is not None else 3
            )
            result = await self.executor.start(
                contract,
                execution.id,
                sandbox=sandbox,
                max_parallel_agents=max_parallel_agents,
            )
        except Exception as exc:
            execution.state = TaskState.FAILED
            execution.ended_at = datetime.now(UTC)
            await db.flush()
            await AuditService.log(
                db=db,
                event_type="retry_execution_start_failed",
                task_id=task.id,
                actor="system",
                source="verification_service",
                execution_id=execution.id,
                payload={
                    "execution_id": execution.id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "verification_report": report,
                },
            )
            # If the retry cannot even start, the task cannot recover on its
            # own; move it to terminal FAILED so humans are alerted.
            await StateMachine.atomic_transition(db, task, TaskState.FAILED)
            await db.commit()
            raise RuntimeError(f"retry macro-agent start failed: {exc}") from exc

        execution.macro_agent_run_id = result["run_id"]
        # Persist the new run ID on the task so feedback has a target even when
        # the relationship is not loaded.
        task.latest_macro_agent_run_id = execution.macro_agent_run_id
        await db.flush()

        await AuditService.log(
            db=db,
            event_type="retry_execution_start",
            task_id=task.id,
            actor="system",
            source="verification_service",
            execution_id=execution.id,
            payload={
                "execution_id": execution.id,
                "macro_agent_run_id": execution.macro_agent_run_id,
            },
        )

    async def _send_macro_agent_feedback(
        self,
        task: Task,
        contract: TaskContract,
        report: dict[str, object],
    ) -> None:
        """Push the verification failure report to the macro-agent run.

        Uses the executor's feedback endpoint when the latest execution has a
        macro-agent run ID.  Failures are logged and audited but do not stop
        the retry: the task is already back in RUNNING.
        """
        run_id = getattr(task, "latest_macro_agent_run_id", None)
        if run_id is None:
            return

        feedback = {
            "controller_task_id": task.id,
            "controller_state": task.state.value,
            "verification_report": report,
            "execution_attempts": task.execution_attempts,
            "max_retries": getattr(contract.execution, "max_retries", 2),
            "objective": contract.objective,
            "acceptance": contract.acceptance,
        }

        try:
            await self.executor.feedback(run_id, feedback)
        except Exception as exc:
            _logger.warning(
                "macro_agent_feedback_failed",
                task_id=task.id,
                run_id=run_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _alert_human_terminal_failure(
        self,
        db: AsyncSession,
        task: Task,
        contract: TaskContract,
        report: dict[str, object],
        reason: str,
    ) -> None:
        """Record a durable audit alert when a task reaches terminal FAILED.

        SPEC-09 §9.6 step 3 requires alerting a human once retry limits are
        exhausted. Phase 1 has no outbound notification channel, so the alert is
        persisted as an ``alert_human`` audit row with the full report. A future
        Phase 2 notification adapter can subscribe to this event type.
        """
        await AuditService.log(
            db=db,
            event_type="alert_human",
            task_id=task.id,
            actor="system",
            source="verification_service",
            payload={
                "reason": reason,
                "task_id": task.id,
                "project_id": contract.project_id,
                "execution_attempts": task.execution_attempts,
                "max_retries": getattr(contract.execution, "max_retries", 2),
                "verification_report": report,
            },
        )
