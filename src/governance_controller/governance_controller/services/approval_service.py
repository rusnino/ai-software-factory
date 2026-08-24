"""Approval service with embedded policy enforcement."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.config import settings
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.opentasks_materializer import (
    MaterializerError,
    OpentasksMaterializer,
)
from governance_controller.services.permission_service import PermissionService
from governance_controller.services.policy_engine import (
    PolicyEngine,
    PolicyResult,
    PolicyViolationError,
)
from governance_controller.services.state_machine import StateMachine

_APPROVAL_TARGET_STATES: dict[ApprovalType, TaskState] = {
    ApprovalType.PLAN: TaskState.PLAN_APPROVED,
    ApprovalType.EXECUTION: TaskState.EXEC_APPROVED,
    ApprovalType.MERGE: TaskState.DONE,
}


class ApprovalService:
    """Record approvals, enforce policy, and advance task state."""

    def __init__(
        self,
        db: AsyncSession,
        policy_engine: type[PolicyEngine] | None = None,
        executor: MacroAgentExecutor | None = None,
        permission_service: PermissionService | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            db: The SQLAlchemy async session to use.
            policy_engine: PolicyEngine class to use for evaluation. Defaults to
                the embedded PolicyEngine.
            executor: Macro-agent executor to invoke on EXECUTION approvals.
            permission_service: Permission validator. Defaults to the embedded
                PermissionService.
        """
        self.db = db
        self.policy_engine = policy_engine or PolicyEngine
        self.executor = executor or MacroAgentExecutor()
        self.permission_service = permission_service or PermissionService()

    async def approve(
        self,
        task: Task,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
        source: str,
        actor: str,
        idempotency_key: str,
        comment: str | None = None,
    ) -> Task:
        """Request an approval, enforce policy, and advance task state.

        Args:
            task: The task being approved.
            contract: The task contract.
            profile: The project profile.
            approval_type: The kind of approval being requested.
            source: The source of the approval (e.g., "plane", "telegram").
            actor: The human actor approving.
            idempotency_key: A unique key for idempotent approval creation.
            comment: Optional human-readable comment.

        Returns:
            The task with its updated state.

        Raises:
            ValueError: If policy evaluation fails, the actor lacks permission,
                or the state transition is invalid.
        """
        # 0. Permission check: agents/system cannot approve, and the proposer
        #    cannot approve their own task.
        if actor == task.proposed_by:
            await self._log_rejection_and_raise(
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "self-approval",
                    "proposed_by": task.proposed_by,
                },
                message="Policy violation(s): actor cannot approve their own task",
                policy_violations=["actor cannot approve their own task"],
            )

        permitted = await self.permission_service.may_approve(
            actor, task.id, approval_type
        )
        if not permitted:
            await self._log_rejection_and_raise(
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "permission_denied",
                },
                message=(
                    "Policy violation(s): "
                    f"{actor} may not request {approval_type.value} approval"
                ),
                policy_violations=[
                    f"{actor} may not request {approval_type.value} approval"
                ],
            )

        # 1. Policy evaluation must happen before any state change or record.
        policy_result: PolicyResult = self.policy_engine.evaluate(
            contract, profile, approval_type
        )
        if not policy_result.allowed:
            await AuditService.log(
                db=self.db,
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "violations": policy_result.violations,
                },
            )
            await self.db.commit()
            raise PolicyViolationError(
                f"Policy violation(s): {', '.join(policy_result.violations)}",
                violations=policy_result.violations,
            )

        # 2. Idempotency: return existing task state if this exact key was
        #    already processed for this exact task, approval type, and actor.
        existing = await self.db.scalar(
            select(Approval).where(
                Approval.idempotency_key == idempotency_key,  # type: ignore[arg-type]
                Approval.task_id == task.id,  # type: ignore[arg-type]
                Approval.approval_type == approval_type.value,  # type: ignore[arg-type]
                Approval.actor == actor,  # type: ignore[arg-type]
            )
        )
        if existing is not None:
            await AuditService.log(
                db=self.db,
                event_type="approval_idempotent",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "idempotency_key": idempotency_key,
                    "previous_state": task.state.value,
                    "new_state": task.state.value,
                },
            )
            return task

        # 3. Validate the state machine transition in-memory first, but do NOT
        #    mutate the task object yet. The actual state advance is done by an
        #    atomic UPDATE with a version/pre-state check below.
        previous_state = task.state
        target_state = _APPROVAL_TARGET_STATES[approval_type]
        StateMachine.validate_transition(previous_state, target_state)

        # 4. Atomically advance the task state. The WHERE clause checks the
        #    expected version and pre-approval state. If another caller already
        #    advanced the task, rowcount is 0 and we fail without triggering
        #    the macro-agent. This works on both PostgreSQL (with row locking
        #    at the database level) and SQLite (which serializes writes).
        from sqlalchemy import update

        new_version = task.version + 1
        result = await self.db.execute(
            update(Task)
            .where(
                Task.id == task.id,  # type: ignore[arg-type]
                Task.version == task.version,  # type: ignore[arg-type]
                Task.state == previous_state.value,  # type: ignore[arg-type]
            )
            .values(
                state=target_state.value,
                version=new_version,
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            await self._log_rejection_and_raise(
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "concurrent_modification",
                    "expected_version": task.version,
                    "expected_state": previous_state.value,
                    "target_state": target_state.value,
                },
                message=(
                    "Concurrent modification detected: task state changed "
                    f"during approval ({previous_state.value} -> {target_state.value})"
                ),
            )

        # The database update succeeded; now reflect it on the in-memory object.
        task.state = target_state
        task.version = new_version
        task.updated_at = datetime.now(UTC)

        await AuditService.log(
            db=self.db,
            event_type="state_change",
            task_id=task.id,
            actor=actor,
            source=source,
            payload={
                "previous_state": previous_state.value,
                "new_state": target_state.value,
                "approval_type": approval_type.value,
            },
        )

        # 5. Record approval.
        approval = Approval(
            task_id=task.id,
            approval_type=approval_type,
            source=source,
            actor=actor,
            comment=comment,
            idempotency_key=idempotency_key,
        )
        self.db.add(approval)
        await self.db.flush()

        await AuditService.log(
            db=self.db,
            event_type="approval",
            task_id=task.id,
            actor=actor,
            source=source,
            payload={
                "approval_type": approval_type.value,
                "idempotency_key": idempotency_key,
                "previous_state": previous_state.value,
                "new_state": target_state.value,
            },
        )

        if approval_type == ApprovalType.EXECUTION:
            return await self._trigger_execution(
                task, contract, profile, actor, source, previous_state
            )

        return task

    async def _log_rejection_and_raise(
        self,
        event_type: str,
        task_id: str,
        actor: str,
        source: str,
        payload: dict[str, Any],
        message: str,
        policy_violations: list[str] | None = None,
    ) -> None:
        """Persist an audit log entry for a rejection, then raise an exception.

        ``get_db()`` rolls back the containing transaction whenever an
        exception propagates out of an endpoint. If a rejection is recorded
        with only ``db.add``/``flush``, the audit row is discarded along with
        the rest of the transaction. This helper flushes and commits the audit
        entry before raising so the rejection is durably recorded.

        When ``policy_violations`` is provided, a ``PolicyViolationError`` is
        raised so callers can map governance denials to ``403`` instead of
        ``422``.
        """
        await AuditService.log(
            db=self.db,
            event_type=event_type,
            task_id=task_id,
            actor=actor,
            source=source,
            payload=payload,
        )
        # Always commit so the audit row survives the rollback triggered when
        # the exception propagates out of ``get_db()``. Tests that drive
        # approval service directly through a session wrapped in
        # ``async with session.begin()`` may need to handle the now-committed
        # state; production path requires this commit for durable audit.
        await self.db.commit()
        if policy_violations is not None:
            raise PolicyViolationError(message, violations=policy_violations)
        raise ValueError(message)

    async def _trigger_execution(
        self,
        task: Task,
        contract: TaskContract,
        profile: ProjectProfile,
        actor: str,
        source: str,
        previous_state: TaskState,
    ) -> Task:
        """Move task to READY, start macro-agent, record Execution, go RUNNING.

        The ``READY`` transition and ``Execution`` row are committed *before*
        the live macro-agent HTTP call so that no database row lock is held
        across that outbound request. This keeps the fail-fast CAS semantics
        intact and prevents a slow/hung macro-agent from pinning the task row.
        """
        # Atomically advance EXEC_APPROVED -> READY. If another caller already
        # moved the task, the UPDATE affects 0 rows and we fail loudly.
        if not await StateMachine.atomic_transition(self.db, task, TaskState.READY):
            await AuditService.log(
                db=self.db,
                event_type="concurrent_modification",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": ApprovalType.EXECUTION.value,
                    "expected_state": TaskState.EXEC_APPROVED.value,
                    "target_state": TaskState.READY.value,
                },
            )
            await self.db.commit()
            raise ValueError(
                "Concurrent modification detected: "
                "task state changed during execution trigger"
            )

        await AuditService.log(
            db=self.db,
            event_type="state_change",
            task_id=task.id,
            actor=actor,
            source=source,
            payload={
                "previous_state": previous_state.value,
                "new_state": TaskState.READY.value,
                "approval_type": ApprovalType.EXECUTION.value,
            },
        )

        from governance_controller.models import Execution

        started_at = datetime.now(UTC)
        execution = Execution(
            id=str(uuid4()),
            task_id=task.id,
            state=TaskState.READY,
            started_at=started_at,
        )
        self.db.add(execution)
        await self.db.flush()

        # CRITICAL: commit here so the row lock from the READY UPDATE is
        # released before the live, potentially slow macro-agent call.
        await self.db.commit()

        # Materialize the approved runtime DAG from Plane when configured.
        opentasks_dag: dict[str, object] | None = None
        if settings.plane_base_url:
            try:
                dag = await OpentasksMaterializer().materialize(
                    root_plane_task_id=task.id,
                    project_id=task.project_id,
                )
                opentasks_dag = dag.model_dump(mode="json")
            except MaterializerError as exc:
                await AuditService.log(
                    db=self.db,
                    event_type="opentasks_materialization_failed",
                    task_id=task.id,
                    actor=actor,
                    source=source,
                    execution_id=execution.id,
                    payload={"error": str(exc)},
                )
                await self.db.commit()
                raise RuntimeError(
                    f"Failed to materialize opentasks DAG: {exc}"
                ) from exc

        if opentasks_dag is not None:
            contract.opentasks_dag = opentasks_dag

        try:
            result = await self.executor.start(
                contract,
                execution.id,
                sandbox=profile.execution.sandbox,
                max_parallel_agents=profile.execution.max_parallel_agents,
            )
        except Exception as exc:  # pragma: no cover - broad error shield
            if await StateMachine.atomic_transition(self.db, task, TaskState.FAILED):
                execution.state = TaskState.FAILED
                execution.ended_at = datetime.now(UTC)
                await self.db.flush()

                await AuditService.log(
                    db=self.db,
                    event_type="execution_start_failed",
                    task_id=task.id,
                    actor=actor,
                    source=source,
                    execution_id=execution.id,
                    payload={
                        "approval_type": ApprovalType.EXECUTION.value,
                        "execution_id": execution.id,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                await AuditService.log(
                    db=self.db,
                    event_type="concurrent_modification",
                    task_id=task.id,
                    actor=actor,
                    source=source,
                    execution_id=execution.id,
                    payload={
                        "approval_type": ApprovalType.EXECUTION.value,
                        "expected_state": TaskState.READY.value,
                        "target_state": TaskState.FAILED.value,
                    },
                )
            await self.db.commit()
            raise RuntimeError(f"macro-agent start failed: {exc}") from exc

        execution.macro_agent_run_id = result["run_id"]
        execution.state = TaskState.RUNNING
        await self.db.flush()

        if not await StateMachine.atomic_transition(self.db, task, TaskState.RUNNING):
            await AuditService.log(
                db=self.db,
                event_type="concurrent_modification",
                task_id=task.id,
                actor=actor,
                source=source,
                execution_id=execution.id,
                payload={
                    "approval_type": ApprovalType.EXECUTION.value,
                    "expected_state": TaskState.READY.value,
                    "target_state": TaskState.RUNNING.value,
                },
            )
            await self.db.commit()
            raise ValueError(
                "Concurrent modification detected: task state changed before RUNNING"
            )

        await AuditService.log(
            db=self.db,
            event_type="state_change",
            task_id=task.id,
            actor=actor,
            source=source,
            execution_id=execution.id,
            payload={
                "previous_state": TaskState.READY.value,
                "new_state": TaskState.RUNNING.value,
                "execution_id": execution.id,
            },
        )

        await AuditService.log(
            db=self.db,
            event_type="execution_start",
            task_id=task.id,
            actor=actor,
            source=source,
            execution_id=execution.id,
            payload={
                "execution_id": execution.id,
                "macro_agent_run_id": execution.macro_agent_run_id,
            },
        )

        return task
