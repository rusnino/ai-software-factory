"""Approval service with embedded policy enforcement."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.permission_service import PermissionService
from governance_controller.services.policy_engine import PolicyEngine, PolicyResult
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
            await AuditService.log(
                db=self.db,
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "self-approval",
                    "proposed_by": task.proposed_by,
                },
            )
            raise ValueError(
                "Policy violation(s): actor cannot approve their own task"
            )

        permitted = await self.permission_service.may_approve(
            actor, task.id, approval_type
        )
        if not permitted:
            await AuditService.log(
                db=self.db,
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "permission_denied",
                },
            )
            raise ValueError(
                "Policy violation(s): "
                f"{actor} may not request {approval_type.value} approval"
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
            raise ValueError(
                f"Policy violation(s): {', '.join(policy_result.violations)}"
            )

        # 2. Idempotency: return existing task state if this exact key was
        #    already processed.
        existing = await self.db.scalar(
            select(Approval).where(
                Approval.idempotency_key == idempotency_key  # type: ignore[arg-type]
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
            await AuditService.log(
                db=self.db,
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
            )
            raise ValueError(
                "Concurrent modification detected: task state changed during approval "
                f"({previous_state.value} -> {target_state.value})"
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
                task, contract, actor, source, previous_state
            )

        return task

    async def _trigger_execution(
        self,
        task: Task,
        contract: TaskContract,
        actor: str,
        source: str,
        previous_state: TaskState,
    ) -> Task:
        """Move task to READY, start macro-agent, record Execution, go RUNNING."""
        # Atomically advance EXEC_APPROVED -> READY. If another caller already
        # moved the task, the UPDATE affects 0 rows and we fail loudly.
        if not await StateMachine.atomic_transition(
            self.db, task, TaskState.READY
        ):
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

        try:
            result = await self.executor.start(contract, execution.id)
        except Exception as exc:  # pragma: no cover - broad error shield
            if await StateMachine.atomic_transition(
                self.db, task, TaskState.FAILED
            ):
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
            raise RuntimeError(f"macro-agent start failed: {exc}") from exc

        execution.macro_agent_run_id = result["run_id"]
        execution.state = TaskState.RUNNING
        await self.db.flush()

        if not await StateMachine.atomic_transition(
            self.db, task, TaskState.RUNNING
        ):
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
