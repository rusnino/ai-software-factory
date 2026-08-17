"""Approval service with embedded policy enforcement."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.policy_engine import PolicyEngine, PolicyResult
from governance_controller.services.state_machine import StateMachine


def _noop_audit(event: str, details: dict[str, Any]) -> None:
    """Temporary no-op audit helper; replaced by AuditService in Task 6."""


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
        audit: Any = None,
    ) -> None:
        """Initialize the service.

        Args:
            db: The SQLAlchemy async session to use.
            policy_engine: PolicyEngine class to use for evaluation. Defaults to
                the embedded PolicyEngine.
            audit: Audit service or helper to call for audit events. Defaults to
                a no-op helper until Task 6 wires the real AuditService.
        """
        self.db = db
        self.policy_engine = policy_engine or PolicyEngine
        self.audit = audit or _noop_audit

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
            ValueError: If policy evaluation fails or the state transition is
                invalid.
        """
        # 1. Policy evaluation must happen before any state change or record.
        policy_result: PolicyResult = self.policy_engine.evaluate(
            contract, profile, approval_type
        )
        if not policy_result.allowed:
            self.audit(
                "approval_rejected",
                {
                    "task_id": task.id,
                    "approval_type": approval_type.value,
                    "actor": actor,
                    "violations": policy_result.violations,
                },
            )
            raise ValueError(
                f"Policy violation(s): {', '.join(policy_result.violations)}"
            )

        # 2. Idempotency: return existing task state if the same actor already
        #    approved this task/type.
        existing = await self.db.scalar(
            select(Approval).where(
                Approval.task_id == task.id,
                Approval.approval_type == approval_type,
                Approval.actor == actor,
            )
        )
        if existing is not None:
            self.audit(
                "approval_idempotent",
                {
                    "task_id": task.id,
                    "approval_type": approval_type.value,
                    "actor": actor,
                },
            )
            return task

        # 3. Determine target state and advance state machine.
        target_state = _APPROVAL_TARGET_STATES[approval_type]
        StateMachine.transition(task, target_state)
        task.updated_at = datetime.now(UTC)

        # 4. Record approval.
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

        self.audit(
            "approval_recorded",
            {
                "task_id": task.id,
                "approval_type": approval_type.value,
                "actor": actor,
                "target_state": target_state.value,
            },
        )

        return task
