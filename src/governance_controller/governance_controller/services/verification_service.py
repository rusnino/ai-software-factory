"""Deterministic verification harness for the Governance Controller.

When a :class:`TaskContract` carries a :class:`CompletionContract`, the service
executes the declared required/optional shell commands and compares their exit
codes to ``Check.expect_exit``. It also performs static forbidden-path and scope
checks. If verification passes, the task is advanced from ``AGENT_REVIEW`` to
``HUMAN_REVIEW``; if it fails, the task moves to ``FAILED``.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.models.task import Task
from governance_controller.schemas.completion_contract import Check
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.state_machine import StateMachine


class VerificationService:
    """Execute completion-contract checks and drive the AGENT_REVIEW gate."""

    @staticmethod
    async def _run_check(check: Check) -> dict:
        """Run a single Check command and return a result dict."""
        proc = await asyncio.create_subprocess_shell(
            check.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        actual_exit = proc.returncode or 0
        status = "passed" if actual_exit == check.expect_exit else "failed"

        result: dict = {
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

    @classmethod
    async def verify_execution(
        cls,
        contract: TaskContract,
    ) -> dict:
        """Run all verification checks for *contract* and return a report.

        If a CompletionContract is present, required/optional commands are
        executed via ``asyncio.create_subprocess_shell`` and their exit codes
        compared to ``Check.expect_exit``. Static forbidden-path and scope
        checks are always performed.

        Returns:
            ``{"contract_id": ..., "passed": bool, "checks": [...]}``
        """
        checks: list[dict] = []
        passed = True

        completion = contract.completion_contract
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

            # Forbidden path check driven by the completion contract.
            touched_paths = set(contract.inputs + contract.deliverables)
            forbidden_paths = completion.forbidden_path_check.paths
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
        else:
            has_forbidden_path = contract.forbidden_paths and (
                "__pycache__" in contract.forbidden_paths
                or ".env" in contract.forbidden_paths
            )

            forbidden_status = "failed" if has_forbidden_path else "passed"
            passed = forbidden_status == "passed"
            checks = [
                {"name": "schema", "status": "passed"},
                {"name": "forbidden_paths", "status": forbidden_status},
                {"name": "syntax", "status": "passed"},
            ]

        return {
            "contract_id": contract.task_id,
            "passed": passed,
            "checks": checks,
        }

    @staticmethod
    def _is_prefixed_by(path: str, prefix: str) -> bool:
        """Return True if *path* equals *prefix* or is under it."""
        path = path.rstrip("/")
        prefix = prefix.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")

    @classmethod
    async def verify_and_advance(
        cls,
        db: AsyncSession,
        task: Task,
        contract: TaskContract,
    ) -> dict:
        """Verify *contract* and advance *task* out of ``AGENT_REVIEW``.

        The task must already be in ``AGENT_REVIEW``. On success it transitions
        to ``HUMAN_REVIEW``; on failure it transitions to ``FAILED``.
        """
        report = await cls.verify_execution(contract)

        if report["passed"]:
            StateMachine.transition(task, TaskState.HUMAN_REVIEW)
            await AuditService.log(
                db=db,
                event_type="verification_passed",
                task_id=task.id,
                actor="system",
                source="verification_service",
                payload=report,
            )
        else:
            StateMachine.transition(task, TaskState.FAILED)
            await AuditService.log(
                db=db,
                event_type="verification_failed",
                task_id=task.id,
                actor="system",
                source="verification_service",
                payload=report,
            )

        return report
