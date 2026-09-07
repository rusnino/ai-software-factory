"""Approval service with embedded policy enforcement."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.config import settings
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import begin_sqlite_cancellation_claim
from governance_controller.models.approval import Approval
from governance_controller.models.task import Task
from governance_controller.schemas.macro_agent import MacroAgentStartResponse
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.opentasks_materializer import (
    MaterializerError,
    OpentasksMaterializer,
)
from governance_controller.services.permission_service import PermissionService
from governance_controller.services.plane_projection import (
    PlaneProjectionService,
    acquire_plane_projection_lock,
)
from governance_controller.services.policy_engine import (
    PolicyEngine,
    PolicyResult,
    PolicyViolationError,
)
from governance_controller.services.policy_engine_backend import (
    PolicyEngineBackend,
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
        policy_backend: PolicyEngineBackend | None = None,
        plane_projection: PlaneProjectionService | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            db: The SQLAlchemy async session to use.
            policy_engine: PolicyEngine class to use for evaluation. Defaults to
                the embedded PolicyEngine.
            executor: Macro-agent executor to invoke on EXECUTION approvals.
            permission_service: Permission validator. Defaults to the embedded
                PermissionService.
            policy_backend: Optional pluggable policy backend. Defaults to one
                that uses OPA when configured, otherwise the embedded engine.
        """
        self.db = db
        self.policy_engine = policy_engine or PolicyEngine
        self.executor = executor or MacroAgentExecutor()
        self.permission_service = permission_service or PermissionService()
        self.policy_backend = policy_backend or PolicyEngineBackend(
            opa_client=None,
        )
        self.plane_projection = plane_projection

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
        #    cannot approve their own task. Normalize identifiers so casing or
        #    whitespace cannot bypass the self-approval guard.
        normalized_actor = PermissionService._normalize_actor(actor)
        if normalized_actor == PermissionService._normalize_actor(task.proposed_by):
            await self._log_rejection_and_raise(
                event_type="approval_rejected",
                task_id=task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "reason": "self-approval",
                    "proposed_by": task.proposed_by,
                    "actor": actor,
                },
                message="Policy violation(s): actor cannot approve their own task",
                policy_violations=["actor cannot approve their own task"],
            )

        permitted = await self.permission_service.may_approve(
            normalized_actor, task.id, approval_type
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
        policy_result: PolicyResult = await self.policy_backend.evaluate(
            contract,
            profile,
            approval_type,
            actor=actor,
            policy_engine=self.policy_engine,
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
            # Re-fetch the task's current state so a duplicate delivery returns
            # the true post-approval state, not the caller's stale in-memory copy
            # (#242 / #278). Use populate_existing to bypass the identity map.
            fresh_result = await self.db.execute(
                select(Task)
                .where(Task.id == task.id)  # type: ignore[arg-type]
                .execution_options(populate_existing=True)
            )
            fresh_task: Task | None = fresh_result.scalar_one_or_none()
            if fresh_task is None:
                fresh_task = task
            await AuditService.log(
                db=self.db,
                event_type="approval_idempotent",
                task_id=fresh_task.id,
                actor=actor,
                source=source,
                payload={
                    "approval_type": approval_type.value,
                    "idempotency_key": idempotency_key,
                    "previous_state": fresh_task.state.value,
                    "new_state": fresh_task.state.value,
                },
            )
            return fresh_task

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
            # Persist the handoff intent before any cancellable work. The poller
            # can resume this approval if the request is cancelled after the
            # approval commit but before _trigger_execution begins.
            await AuditService.log(
                db=self.db,
                event_type="execution_start_pending",
                task_id=task.id,
                actor="system",
                source="approval_service",
                payload={
                    "approval_type": approval_type.value,
                    "state": target_state.value,
                },
            )
            await self.db.commit()
            return await self._trigger_execution(
                task, contract, profile, actor, source, previous_state
            )

        await self._project_state_to_plane(
            task=task,
            state=target_state,
            approval_type=approval_type,
            opentasks_id=None,
        )

        return task

    async def _project_state_to_plane(
        self,
        task: Task,
        state: TaskState,
        approval_type: ApprovalType,
        opentasks_id: str | None = None,
    ) -> None:
        """Project the new Controller state to Plane when configured.

        Failures are logged and swallowed so a Plane projection outage does not
        block the authoritative Controller state machine.
        """
        projection = self.plane_projection
        if projection is None and settings.plane_base_url:
            projection = PlaneProjectionService()
        if projection is None:
            return

        pending = await AuditService.log(
            db=self.db,
            event_type="plane_projection_pending",
            task_id=task.id,
            actor="system",
            source="approval_service",
            payload={
                "operation": "update_state",
                "state": state.value,
                "approval_type": approval_type.value,
                "plane_issue_id": task.plane_issue_id or task.id,
            },
        )
        # The Postgres audit-tip lock is transaction-scoped. Commit the
        # authoritative Controller state before non-authoritative Plane I/O so
        # a slow projection cannot hold the global lock for another task.
        await self.db.commit()
        # Serialize projections for this task without blocking unrelated task
        # transitions. The fresh read prevents a projection that waited behind
        # a newer transition from sending its stale requested state.
        await acquire_plane_projection_lock(self.db, task.id)
        fresh_result = await self.db.execute(
            select(Task)
            .where(Task.id == task.id)  # type: ignore[arg-type]
            .execution_options(populate_existing=True)
        )
        fresh_task = fresh_result.scalar_one_or_none()
        if fresh_task is not None:
            task = fresh_task
            state = fresh_task.state

        try:
            await projection.update_state(
                controller_task_id=task.id,
                plane_issue_id=task.plane_issue_id or task.id,
                state=state,
                project_id=task.project_id,
                opentasks_id=opentasks_id,
            )
        except Exception as exc:
            await AuditService.log(
                db=self.db,
                event_type="plane_projection_failed",
                task_id=task.id,
                actor="system",
                source="approval_service",
                payload={
                    "state": state.value,
                    "approval_type": approval_type.value,
                    "plane_issue_id": task.plane_issue_id,
                    "pending_event_id": pending.event_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            await self.db.commit()
        else:
            await AuditService.log(
                db=self.db,
                event_type="plane_projection_completed",
                task_id=task.id,
                actor="system",
                source="approval_service",
                payload={
                    "operation": "update_state",
                    "pending_event_id": pending.event_id,
                    "plane_issue_id": task.plane_issue_id or task.id,
                },
            )
            # Release the task-scoped advisory lock after the external call.
            await self.db.commit()

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

        # Capture the actual state immediately before the READY transition for
        # the audit log, not the stale previous_state from the approval call.
        ready_previous_state = TaskState.EXEC_APPROVED
        await AuditService.log(
            db=self.db,
            event_type="state_change",
            task_id=task.id,
            actor=actor,
            source=source,
            payload={
                "previous_state": ready_previous_state.value,
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
        opentasks_id: str | None = None
        opentasks_dag: dict[str, object] | None = None
        if settings.plane_base_url:
            try:
                dag = await OpentasksMaterializer().materialize(
                    root_plane_task_id=task.plane_issue_id or task.id,
                    project_id=task.project_id,
                )
                opentasks_dag = dag.model_dump(mode="json")
                if dag.tasks:
                    opentasks_id = dag.tasks[0].id
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
            macro_agent_run_id = MacroAgentStartResponse.model_validate(
                result
            ).run_id
        except Exception as exc:  # pragma: no cover - broad error shield
            transitioned = await StateMachine.atomic_transition(
                self.db, task, TaskState.FAILED
            )
            # The external start failed, so this local execution is terminal
            # regardless of whether another writer won the task CAS.
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
            if not transitioned:
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

        # Do NOT set the Execution success fields until the Task-level CAS to
        # RUNNING wins. If the CAS loses (e.g. the poller already moved the task
        # to FAILED), flushing early would commit a RUNNING Execution row for a
        # FAILED task and orphan the real macro-agent run (#262).
        if not await StateMachine.atomic_transition(self.db, task, TaskState.RUNNING):
            # The external run already exists even though this caller lost the
            # task CAS. Persist its ID and cleanup intent together before any
            # cleanup request so a crash cannot strand the run outside recovery.
            execution.state = TaskState.FAILED
            execution.ended_at = datetime.now(UTC)
            execution.macro_agent_run_id = macro_agent_run_id
            execution.cancellation_pending = True
            await self.db.flush()
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
                    "macro_agent_run_id": macro_agent_run_id,
                },
            )
            await AuditService.log(
                db=self.db,
                event_type="execution_cancel_pending",
                task_id=task.id,
                actor=actor,
                source=source,
                execution_id=execution.id,
                payload={
                    "macro_agent_run_id": macro_agent_run_id,
                    "reason": "execution_start_cas_lost",
                },
            )
            await self.db.commit()

            # Do not cancel a run if a concurrent winner legitimately attached
            # this exact ID while the CAS result was being handled. Match the
            # poller's lock order before holding both rows through cleanup.
            await begin_sqlite_cancellation_claim(self.db)
            execution_stmt = (
                select(Execution)
                .where(Execution.id == execution.id)  # type: ignore[arg-type]
                .execution_options(populate_existing=True)
            )
            if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
                execution_stmt = execution_stmt.with_for_update()
            execution = (await self.db.execute(execution_stmt)).scalar_one()

            # Lock the authoritative task row through the cleanup decision so a
            # pointer update cannot race between this check and cancellation.
            fresh_stmt = (
                select(Task)
                .where(Task.id == task.id)  # type: ignore[arg-type]
                .execution_options(populate_existing=True)
            )
            if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
                fresh_stmt = fresh_stmt.with_for_update()
            fresh_result = await self.db.execute(fresh_stmt)
            fresh_task = fresh_result.scalar_one_or_none()
            if not execution.cancellation_pending:
                # The poller completed this durable cleanup intent while the
                # loser was reacquiring its rows. Do not repeat its cancel or
                # completion audit.
                await self.db.commit()
            elif fresh_task is None or (
                fresh_task.state is not TaskState.RUNNING
                or fresh_task.latest_macro_agent_run_id != macro_agent_run_id
            ):
                # The execution row is the durable single-flight claim. Keep
                # the existing execution -> task lock order and retain those
                # locks through cancellation; without an outbox or a separate
                # claim state, releasing them would let the poller duplicate
                # the external request.
                try:
                    await self.executor.cancel(macro_agent_run_id)
                except Exception as cleanup_exc:  # pragma: no cover - boundary shield
                    await AuditService.log(
                        db=self.db,
                        event_type="execution_cancel_failed",
                        task_id=task.id,
                        actor=actor,
                        source=source,
                        execution_id=execution.id,
                        payload={
                            "macro_agent_run_id": macro_agent_run_id,
                            "error": str(cleanup_exc),
                            "error_type": type(cleanup_exc).__name__,
                        },
                    )
                    await self.db.commit()
                else:
                    execution.cancellation_pending = False
                    await self.db.flush()
                    await AuditService.log(
                        db=self.db,
                        event_type="execution_cancel_completed",
                        task_id=task.id,
                        actor=actor,
                        source=source,
                        execution_id=execution.id,
                        payload={"macro_agent_run_id": macro_agent_run_id},
                    )
                    await self.db.commit()
            else:
                execution.cancellation_pending = False
                await self.db.flush()
                await AuditService.log(
                    db=self.db,
                    event_type="execution_cancel_completed",
                    task_id=task.id,
                    actor=actor,
                    source=source,
                    execution_id=execution.id,
                    payload={
                        "macro_agent_run_id": macro_agent_run_id,
                        "reason": "run_attached_to_current_task",
                    },
                )
                await self.db.commit()
            raise ValueError(
                "Concurrent modification detected: task state changed before RUNNING"
            )

        execution.macro_agent_run_id = macro_agent_run_id
        execution.state = TaskState.RUNNING
        task.latest_macro_agent_run_id = macro_agent_run_id
        await self.db.flush()

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

        await self._project_state_to_plane(
            task=task,
            state=TaskState.RUNNING,
            approval_type=ApprovalType.EXECUTION,
            opentasks_id=opentasks_id,
        )

        return task
