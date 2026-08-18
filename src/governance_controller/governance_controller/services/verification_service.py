"""Deterministic verification harness for the Governance Controller.

When a :class:`TaskContract` carries a :class:`CompletionContract`, the service
executes the declared required/optional shell commands and compares their exit
codes to ``Check.expect_exit``. It also performs static forbidden-path and scope
checks. If verification passes, the task is advanced from ``AGENT_REVIEW`` to
``HUMAN_REVIEW``; if it fails, the task moves to ``FAILED``.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.schemas.completion_contract import Check
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.state_machine import StateMachine
from governance_controller.utils.paths import normalize_path


class VerificationService:
    """Execute completion-contract checks and drive the AGENT_REVIEW gate."""

    def __init__(
        self,
        executor: MacroAgentExecutor | None = None,
    ) -> None:
        self.executor = executor or MacroAgentExecutor()

    @staticmethod
    async def _run_check(check: Check) -> dict[str, object]:
        """Run a single Check command and return a result dict."""
        proc = await asyncio.create_subprocess_shell(
            check.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        actual_exit = proc.returncode or 0
        status = "passed" if actual_exit == check.expect_exit else "failed"

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
    ) -> dict[str, object]:
        """Run all verification checks for *contract* and return a report.

        Verification commands declared in ``TaskContract.verification``
        (SPEC-03 §3.5) are always executed. If a CompletionContract is also
        present, its required/optional commands are executed via
        ``asyncio.create_subprocess_shell`` and their exit codes compared to
        ``Check.expect_exit``. Static forbidden-path and scope checks are
        performed when a CompletionContract is present; otherwise the task
        contract's own ``forbidden_paths`` are checked.

        Returns:
            ``{"contract_id": ..., "passed": bool, "checks": [...]}``
        """
        checks: list[dict[str, object]] = []
        passed = True

        completion = contract.completion_contract
        touched_paths = set(contract.inputs + contract.deliverables)

        # Always execute TaskContract.verification.commands (SPEC-03 §3.5),
        # even when there is no CompletionContract.
        contract_verification_checks = (
            cls._verification_commands_from_contract(contract)
        )
        for check in contract_verification_checks:
            result = await cls._run_check(check)
            checks.append(result)
            if result["status"] == "failed":
                passed = False

        if completion is not None:
            for check in completion.required:
                result = await cls._run_check(check)
                checks.append(result)
                if result["status"] == "failed":
                    passed = False

            for check in completion.optional:
                result = await cls._run_check(check)
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
        # the CompletionContract (when present). SPEC-03 treats these as the
        # canonical pairing, so the task-level list is always enforced.
        forbidden_paths: list[str] = list(contract.forbidden_paths)
        if completion is not None:
            forbidden_paths = list(
                set(forbidden_paths) | set(completion.forbidden_path_check.paths)
            )
        forbidden_touches = {
            p
            for p in touched_paths
            if any(
                cls._is_prefixed_by(p, forbidden)
                for forbidden in forbidden_paths
            )
        }
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
            scope_forbidden = completion.scope_check.forbidden_paths
            scope_conflicts = {
                p
                for p in touched_paths
                if any(
                    cls._is_prefixed_by(p, prefix)
                    for prefix in scope_forbidden
                )
            }
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
        report = await service.verify_execution(contract)

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
                return report
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
                # Failure feedback to macro-agent remains a Phase 1 TODO
                # (SPEC-09 §9.6 #2); the task is already back in RUNNING.
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
            sandbox = (
                profile.execution.sandbox
                if profile is not None
                else "worktree"
            )
            max_parallel_agents = (
                profile.execution.max_parallel_agents
                if profile is not None
                else 3
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
            await db.commit()
            raise RuntimeError(
                f"retry macro-agent start failed: {exc}"
            ) from exc

        execution.macro_agent_run_id = result["run_id"]
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
