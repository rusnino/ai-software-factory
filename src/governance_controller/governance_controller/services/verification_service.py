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

            # Forbidden path check driven by the completion contract.
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
            # Without a CompletionContract, run the contract-level forbidden_paths
            # static check directly. The proposer-supplied list is used verbatim.
            forbidden_paths = contract.forbidden_paths
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
    ) -> dict[str, object]:
        """Verify *contract* and atomically advance *task* out of ``AGENT_REVIEW``.

        The task must already be in ``AGENT_REVIEW``. On success it transitions
        to ``HUMAN_REVIEW``; on failure it transitions to ``FAILED``. The state
        change uses the same compare-and-swap discipline as ``ApprovalService``
        so concurrent events cannot silently clobber each other.
        """
        report = await cls.verify_execution(contract)

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

        return report
