"""Fallback poller for stuck macro-agent executions.

SPEC-05 §5.6: if the Event Bridge stops delivering events, the Controller must
poll the macro-agent service for execution status and transition tasks that
have exceeded their timeout budget to BLOCKED with a human-alert audit entry.

This poller also recovers from two crash windows identified in #253 and #254:
tasks left at ``READY`` because ``executor.start()`` crashed mid-flight, and
tasks left at ``AGENT_REVIEW`` because verification crashed mid-flight.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.cancellation_service import (
    claim_cancellation,
    release_cancellation_claim,
)
from governance_controller.services.plane_projection import (
    acquire_plane_projection_event_lock,
)
from governance_controller.services.state_machine import StateMachine
from governance_controller.services.task_service import TaskService


class StuckExecutionPoller:
    """Detect and recover macro-agent executions stuck without events.

    The poller looks for tasks in ``RUNNING`` whose latest execution has been
    active longer than a timeout derived from the task contract. If the macro
    agent confirms the run is still active, it is given until the next poll;
    otherwise the task is moved to ``BLOCKED`` and an audit alert is recorded.

    Args:
        db: Async SQLAlchemy session.
        executor: Optional ``MacroAgentExecutor`` override.
        client: Optional ``MacroAgentClient`` override.
    """

    def __init__(
        self,
        db: AsyncSession,
        executor: MacroAgentExecutor | None = None,
        client: MacroAgentClient | None = None,
        dry_run: bool = False,
        batch_size: int = 100,
    ) -> None:
        self.db = db
        self._executor = executor
        self._client = client
        self._dry_run = dry_run
        self._batch_size = batch_size

    _retry_recovery_backoff = timedelta(minutes=1)

    def _timeout_factor(self) -> int:
        return 2

    def _crash_timeout_factor(self) -> int:
        """Multiplier for crash-recovery windows (#253/#254)."""
        return 2

    def _verification_crash_timeout_minutes(self) -> int:
        """Separate budget for detecting a crashed verification (#264).

        Verification runs multiple sequential subprocess checks; its wall-clock
        budget is independent of the macro-agent execution timeout configured
        on the task.
        """
        return 15

    async def poll(self) -> list[dict[str, Any]]:
        """Run one polling pass and return a list of actions taken."""
        actions: list[dict[str, Any]] = []

        cancellation_actions = await self._poll_pending_cancellations()
        actions.extend(cancellation_actions)

        approved_start_actions = await self._poll_execution_start_pending()
        actions.extend(approved_start_actions)

        retry_start_actions = await self._poll_verification_retry_pending()
        actions.extend(retry_start_actions)

        retry_start_actions = await self._poll_retry_start()
        actions.extend(retry_start_actions)

        running_actions = await self._poll_running()
        actions.extend(running_actions)

        ready_actions = await self._poll_ready()
        actions.extend(ready_actions)

        agent_review_actions = await self._poll_agent_review()
        actions.extend(agent_review_actions)

        plane_projection_actions = await self._poll_plane_projection_pending()
        actions.extend(plane_projection_actions)

        return actions

    def _executor_for_recovery(self) -> MacroAgentExecutor:
        """Return the configured executor, preserving injected test doubles."""
        if self._executor is not None:
            return self._executor
        return MacroAgentExecutor(client=self._client)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Treat legacy database timestamps without timezone as UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _task_contract(task: Task) -> TaskContract:
        """Validate and reconstruct a contract persisted on a task row."""
        return TaskContract(**cast(dict[str, Any], task.task_contract_json))

    def _is_postgres(self) -> bool:
        """Return True when the backing dialect is PostgreSQL."""
        return self.db.bind.dialect.name == "postgresql"

    async def _poll_pending_cancellations(self) -> list[dict[str, Any]]:
        """Retry external cancellations that failed after a CAS loser."""
        actions: list[dict[str, Any]] = []
        stmt = (
            select(Execution)
            .where(
                Execution.__table__.c.cancellation_pending.is_(True)  # type: ignore[attr-defined]
            )
            .order_by(Execution.__table__.c.id)  # type: ignore[attr-defined]
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )
        if self._is_postgres() and not self._dry_run:
            # Keep the bounded batch selection locked until each row gets a
            # durable claim. The claim commit then releases this lock before
            # the external request.
            stmt = stmt.with_for_update(of=Execution, skip_locked=True)
        executions = (await self.db.execute(stmt)).scalars().all()
        # A claim commit or losing-claim rollback can expire the ORM batch.
        # Keep recovery decisions independent of those session state changes.
        execution_rows = [
            (execution.id, execution.task_id, execution.macro_agent_run_id)
            for execution in executions
        ]

        for execution_id, task_id, run_id in execution_rows:
            if not isinstance(run_id, str) or not run_id:
                continue

            if self._dry_run:
                task = await self.db.scalar(
                    select(Task)
                    .where(Task.id == task_id)  # type: ignore[arg-type]
                    .execution_options(populate_existing=True)
                )
                task_owns_run = (
                    task is not None
                    and task.state is TaskState.RUNNING
                    and task.latest_macro_agent_run_id == run_id
                )
                if task_owns_run:
                    actions.append(
                        {
                            "task_id": task_id,
                            "execution_id": execution_id,
                            "action": "would_complete_execution_cancel",
                            "macro_agent_run_id": run_id,
                            "reason": "run_attached_to_current_task",
                        }
                    )
                else:
                    actions.append(
                        {
                            "task_id": task_id,
                            "execution_id": execution_id,
                            "action": "would_cancel_pending_execution",
                            "macro_agent_run_id": run_id,
                        }
                    )
                continue

            claim = await claim_cancellation(
                self.db,
                execution_id,
                run_id,
                task_id=task_id,
                execution_already_locked=self._is_postgres(),
            )
            if claim is None:
                continue

            if claim.task_owns_run:
                action = {
                    "task_id": task_id,
                    "execution_id": execution_id,
                    "action": "execution_cancel_completed",
                    "macro_agent_run_id": run_id,
                    "reason": "run_attached_to_current_task",
                }
                if await release_cancellation_claim(
                    self.db, claim, completed=True
                ):
                    await AuditService.log(
                        db=self.db,
                        event_type="execution_cancel_completed",
                        task_id=task_id,
                        actor="system",
                        source="stuck_execution_poller",
                        execution_id=execution_id,
                        payload={
                            "macro_agent_run_id": run_id,
                            "reason": "run_attached_to_current_task",
                        },
                    )
                    actions.append(action)
                    await self.db.commit()
                else:
                    await self.db.rollback()
                continue

            action = {
                "task_id": task_id,
                "execution_id": execution_id,
                "action": "would_cancel_pending_execution",
                "macro_agent_run_id": run_id,
            }
            try:
                await self._executor_for_recovery().cancel(run_id)
            except Exception as exc:  # pragma: no cover - boundary shield
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 404
                ):
                    action = {
                        **action,
                        "action": "execution_cancel_completed",
                        "reason": "run_not_found",
                    }
                    if await release_cancellation_claim(
                        self.db, claim, completed=True
                    ):
                        await AuditService.log(
                            db=self.db,
                            event_type="execution_cancel_completed",
                            task_id=task_id,
                            actor="system",
                            source="stuck_execution_poller",
                            execution_id=execution_id,
                            payload={
                                "macro_agent_run_id": run_id,
                                "reason": "run_not_found",
                            },
                        )
                        actions.append(action)
                        await self.db.commit()
                    else:
                        await self.db.rollback()
                    continue
                failed_action = {
                    **action,
                    "action": "execution_cancel_failed",
                }
                if await release_cancellation_claim(
                    self.db, claim, completed=False
                ):
                    await AuditService.log(
                        db=self.db,
                        event_type="execution_cancel_failed",
                        task_id=task_id,
                        actor="system",
                        source="stuck_execution_poller",
                        execution_id=execution_id,
                        payload={
                            "macro_agent_run_id": run_id,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )
                    actions.append(failed_action)
                    await self.db.commit()
                else:
                    await self.db.rollback()
                continue

            completed_action = {**action, "action": "execution_cancel_completed"}
            if await release_cancellation_claim(self.db, claim, completed=True):
                await AuditService.log(
                    db=self.db,
                    event_type="execution_cancel_completed",
                    task_id=task_id,
                    actor="system",
                    source="stuck_execution_poller",
                    execution_id=execution_id,
                    payload={"macro_agent_run_id": run_id},
                )
                actions.append(completed_action)
                await self.db.commit()
            else:
                await self.db.rollback()

        return actions

    async def _poll_execution_start_pending(self) -> list[dict[str, Any]]:
        """Resume approved execution starts interrupted before READY."""
        terminal = aliased(AuditLog)
        terminal_exists = exists().where(
            terminal.task_id == AuditLog.task_id,  # type: ignore[arg-type]
            terminal.payload["pending_event_id"].as_string()
            == AuditLog.event_id,
            or_(
                terminal.event_type == "execution_start_recovered",  # type: ignore[arg-type]
                and_(
                    terminal.event_type == "execution_start_recovery_failed",  # type: ignore[arg-type]
                    terminal.payload["retryable"].as_boolean().is_(False),
                ),
            ),
        )
        result = await self.db.execute(
            select(Task, AuditLog)
            .join(AuditLog, AuditLog.task_id == Task.id)  # type: ignore[arg-type]
            .where(Task.state == TaskState.EXEC_APPROVED.value)  # type: ignore[arg-type]
            .where(AuditLog.event_type == "execution_start_pending")  # type: ignore[arg-type]
            .where(~terminal_exists)
            .order_by(AuditLog.__table__.c.timestamp)  # type: ignore[attr-defined]
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )

        from governance_controller.services.approval_service import ApprovalService

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, marker in result.tuples().all():
            timeout = await self._task_timeout_minutes(task)
            deadline = self._as_utc(marker.timestamp) + timedelta(
                minutes=timeout * self._crash_timeout_factor()
            )
            if now < deadline:
                continue

            if await self._execution_start_recovery_is_backing_off(
                marker.event_id, now
            ):
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "would_start_execution",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            try:
                contract = self._task_contract(task)
            except Exception as exc:  # pragma: no cover - malformed persisted data
                await self._record_execution_start_recovery_failure(
                    task=task,
                    marker=marker,
                    error=exc,
                    retryable=False,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "execution_start_recovery_failed",
                        "deadline": deadline.isoformat(),
                        "retryable": False,
                    }
                )
                continue

            profile = await TaskService(self.db).get_profile_by_project_id(
                task.project_id
            )
            if profile is None:
                error = RuntimeError(
                    f"Project profile {task.project_id} not found for recovery"
                )
                await self._record_execution_start_recovery_failure(
                    task=task,
                    marker=marker,
                    error=error,
                    retryable=True,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "execution_start_recovery_failed",
                        "deadline": deadline.isoformat(),
                        "retryable": True,
                    }
                )
                continue

            try:
                await ApprovalService(
                    db=self.db,
                    executor=self._executor_for_recovery(),
                )._trigger_execution(
                    task=task,
                    contract=contract,
                    profile=profile,
                    actor="system:poller",
                    source="stuck_execution_poller",
                    previous_state=TaskState.EXEC_APPROVED,
                )
            except Exception as exc:  # pragma: no cover - boundary shield
                await self._record_execution_start_recovery_failure(
                    task=task,
                    marker=marker,
                    error=exc,
                    retryable=True,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "execution_start_recovery_failed",
                        "deadline": deadline.isoformat(),
                        "retryable": True,
                    }
                )
                continue

            await AuditService.log(
                db=self.db,
                event_type="execution_start_recovered",
                task_id=task.id,
                actor="system",
                source="stuck_execution_poller",
                payload={"pending_event_id": marker.event_id},
            )
            await self.db.commit()
            actions.append(
                {
                    "task_id": task.id,
                    "action": "execution_start_recovered",
                    "deadline": deadline.isoformat(),
                }
            )

        return actions

    async def _execution_start_recovery_is_backing_off(
        self,
        pending_event_id: str,
        now: datetime,
    ) -> bool:
        """Return whether the latest retryable start-recovery failure is cooling."""
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.event_type == "execution_start_recovery_failed",  # type: ignore[arg-type]
            )
            .where(
                AuditLog.payload["pending_event_id"].as_string() == pending_event_id
            )
            .order_by(AuditLog.__table__.c.timestamp.desc())  # type: ignore[attr-defined]
        )
        for entry in result.scalars().all():
            if entry.payload.get("retryable") is not True:
                continue
            retry_after = entry.payload.get("retry_after")
            if not isinstance(retry_after, str):
                continue
            try:
                retry_at = datetime.fromisoformat(retry_after)
            except ValueError:
                continue
            return now < self._as_utc(retry_at)
        return False

    async def _record_execution_start_recovery_failure(
        self,
        task: Task,
        marker: AuditLog,
        error: Exception,
        retryable: bool,
    ) -> None:
        """Persist an isolated execution-start recovery failure."""
        payload: dict[str, Any] = {
            "pending_event_id": marker.event_id,
            "error": str(error),
            "error_type": type(error).__name__,
            "retryable": retryable,
        }
        if retryable:
            payload["retry_after"] = (
                datetime.now(UTC) + self._retry_recovery_backoff
            ).isoformat()
        await AuditService.log(
            db=self.db,
            event_type="execution_start_recovery_failed",
            task_id=task.id,
            actor="system",
            source="stuck_execution_poller",
            payload=payload,
        )
        await self.db.commit()

    async def _poll_verification_retry_pending(self) -> list[dict[str, Any]]:
        """Resume a verification retry interrupted before its execution start."""
        terminal = aliased(AuditLog)
        terminal_exists = exists().where(
            terminal.task_id == AuditLog.task_id,  # type: ignore[arg-type]
            terminal.payload["pending_event_id"].as_string()
            == AuditLog.event_id,
            or_(
                terminal.event_type.in_(  # type: ignore[attr-defined]
                    ["verification_retry_recovered", "verification_retry_failed"]
                ),
                and_(
                    terminal.event_type == "verification_retry_recovery_failed",  # type: ignore[arg-type]
                    terminal.payload["retryable"].as_boolean().is_(False),
                ),
                and_(
                    terminal.event_type == "execution_start_failed",  # type: ignore[arg-type]
                    terminal.payload["reason_code"].as_string()
                    == "retry_execution_start_never_completed",
                    terminal.payload["pending_event_id"].as_string()
                    == AuditLog.event_id,
                ),
            ),
        )
        result = await self.db.execute(
            select(Task, AuditLog)
            .join(AuditLog, AuditLog.task_id == Task.id)  # type: ignore[arg-type]
            .where(
                Task.__table__.c.state.in_(  # type: ignore[attr-defined]
                    [TaskState.FAILED.value, TaskState.RUNNING.value]
                )
            )
            .where(AuditLog.event_type == "verification_retry_pending")  # type: ignore[arg-type]
            .where(~terminal_exists)
            .order_by(AuditLog.__table__.c.timestamp)  # type: ignore[attr-defined]
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )

        from governance_controller.services.verification_service import (
            VerificationService,
        )

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, marker in result.tuples().all():
            attempt = marker.payload.get("attempt")
            if not isinstance(attempt, int):
                continue
            timeout = await self._task_timeout_minutes(task)
            deadline = self._as_utc(marker.timestamp) + timedelta(
                minutes=timeout * self._crash_timeout_factor()
            )
            if now < deadline:
                continue

            if await self._retry_marker_has_outcome(task.id, attempt):
                continue

            try:
                contract = self._task_contract(task)
            except Exception as exc:  # pragma: no cover - malformed persisted data
                if self._dry_run:
                    actions.append(
                        {
                            "task_id": task.id,
                            "action": "would_skip_malformed_retry_contract",
                            "deadline": deadline.isoformat(),
                        }
                    )
                    continue
                await self._record_retry_recovery_failure(
                    task=task,
                    marker=marker,
                    attempt=attempt,
                    error=exc,
                    retryable=False,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "verification_retry_recovery_failed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            max_retries = getattr(contract.execution, "max_retries", 2)
            if task.state == TaskState.FAILED:
                if attempt > max_retries or attempt not in {
                    task.execution_attempts,
                    task.execution_attempts + 1,
                }:
                    continue
            elif (
                attempt != task.execution_attempts
                or await self._has_active_execution(task.id)
            ):
                continue

            if await self._retry_recovery_is_backing_off(task.id, attempt, now):
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "would_start_verification_retry",
                        "attempt": attempt,
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            try:
                profile = await TaskService(self.db).get_profile_by_project_id(
                    task.project_id
                )
            except (TypeError, ValidationError) as exc:  # malformed persisted data
                await self._record_retry_recovery_failure(
                    task=task,
                    marker=marker,
                    attempt=attempt,
                    error=exc,
                    retryable=False,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "verification_retry_recovery_failed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue
            if profile is None:
                error = RuntimeError(
                    f"Project profile {task.project_id} not found"
                )
                await self._record_retry_recovery_failure(
                    task=task,
                    marker=marker,
                    attempt=attempt,
                    error=error,
                    retryable=True,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "verification_retry_recovery_failed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            if task.state == TaskState.FAILED:
                transitioned = (
                    await StateMachine.atomic_transition_from_failed_to_running(
                        self.db,
                        task,
                        execution_attempts=attempt,
                    )
                )
                if not transitioned:
                    continue

            report = marker.payload.get("verification_report", {})
            if not isinstance(report, dict):
                report = {}
            try:
                verifier = VerificationService(executor=self._executor_for_recovery())
                started = await verifier._start_retry_execution(
                    db=self.db,
                    task=task,
                    contract=contract,
                    profile=profile,
                    report=report,
                )
                if started is False:
                    actions.append(
                        {
                            "task_id": task.id,
                            "action": "verification_retry_already_claimed",
                            "attempt": attempt,
                            "deadline": deadline.isoformat(),
                        }
                    )
                    continue
            except Exception as exc:  # pragma: no cover - boundary shield
                await self._record_retry_recovery_failure(
                    task=task,
                    marker=marker,
                    attempt=attempt,
                    error=exc,
                    retryable=True,
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "verification_retry_recovery_failed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            await AuditService.log(
                db=self.db,
                event_type="verification_retry_recovered",
                task_id=task.id,
                actor="system",
                source="stuck_execution_poller",
                payload={
                    "pending_event_id": marker.event_id,
                    "attempt": attempt,
                },
            )
            await self.db.commit()
            await verifier._send_macro_agent_feedback(
                task=task,
                contract=contract,
                report=report,
            )
            actions.append(
                {
                    "task_id": task.id,
                    "action": "verification_retry_recovered",
                    "deadline": deadline.isoformat(),
                }
            )

        return actions

    async def _retry_marker_has_outcome(self, task_id: str, attempt: int) -> bool:
        """Return whether a retry marker already has a terminal outcome."""
        result = await self.db.execute(
            select(AuditLog).where(
                AuditLog.task_id == task_id,  # type: ignore[arg-type]
                AuditLog.__table__.c.event_type.in_(  # type: ignore[attr-defined]
                    [
                        "verification_retry_recovered",
                        "verification_retry_failed",
                        "verification_retry_recovery_failed",
                        "execution_start_failed",
                    ]
                ),
            )
        )
        for entry in result.scalars().all():
            if entry.payload.get("attempt") != attempt:
                continue
            if entry.event_type in {
                "verification_retry_recovered",
                "verification_retry_failed",
            }:
                return True
            if (
                entry.event_type == "execution_start_failed"
                and entry.payload.get("reason_code")
                == "retry_execution_start_never_completed"
            ):
                return True
            if (
                entry.event_type == "verification_retry_recovery_failed"
                and entry.payload.get("retryable") is False
            ):
                return True
        return False

    async def _retry_recovery_is_backing_off(
        self,
        task_id: str,
        attempt: int,
        now: datetime,
    ) -> bool:
        """Return whether the latest retryable recovery failure is cooling down."""
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.task_id == task_id,  # type: ignore[arg-type]
                AuditLog.event_type == "verification_retry_recovery_failed",  # type: ignore[arg-type]
            )
            .order_by(AuditLog.__table__.c.timestamp.desc())  # type: ignore[attr-defined]
        )
        for entry in result.scalars().all():
            if entry.payload.get("attempt") != attempt:
                continue
            retry_after = entry.payload.get("retry_after")
            if not isinstance(retry_after, str):
                continue
            try:
                retry_at = datetime.fromisoformat(retry_after)
            except ValueError:
                continue
            return now < self._as_utc(retry_at)
        return False

    async def _record_retry_recovery_failure(
        self,
        task: Task,
        marker: AuditLog,
        attempt: int,
        error: Exception,
        retryable: bool,
    ) -> None:
        """Persist an isolated recovery failure without consuming the marker."""
        payload: dict[str, Any] = {
            "pending_event_id": marker.event_id,
            "attempt": attempt,
            "error": str(error),
            "error_type": type(error).__name__,
            "retryable": retryable,
        }
        if retryable:
            payload["retry_after"] = (
                datetime.now(UTC) + self._retry_recovery_backoff
            ).isoformat()
        await AuditService.log(
            db=self.db,
            event_type="verification_retry_recovery_failed",
            task_id=task.id,
            actor="system",
            source="stuck_execution_poller",
            payload=payload,
        )
        await self.db.commit()

    async def _has_active_execution(self, task_id: str) -> bool:
        """Return whether a task already has a READY/RUNNING execution row."""
        execution = await self.db.scalar(
            select(Execution.__table__.c.id)  # type: ignore[attr-defined]
            .where(Execution.task_id == task_id)  # type: ignore[arg-type]
            .where(
                Execution.__table__.c.state.in_(  # type: ignore[attr-defined]
                    [TaskState.READY.value, TaskState.RUNNING.value]
                )
            )
            .limit(1)
        )
        return execution is not None

    async def _poll_retry_start(self) -> list[dict[str, Any]]:
        """Fail retry executions whose external start never completed (#289).

        Verification uses the internal Execution ID as a temporary task pointer
        while the external macro-agent run ID is unknown. This sentinel must be
        recovered separately: it is not a real run ID and must never be sent to
        the macro-agent status endpoint.
        """
        result = await self.db.execute(
            select(Task, Execution)
            .join(Execution, Execution.task_id == Task.id)  # type: ignore[arg-type]
            .where(Task.state == TaskState.RUNNING.value)  # type: ignore[arg-type]
            .where(Execution.state == TaskState.RUNNING.value)  # type: ignore[arg-type]
            .where(Execution.macro_agent_run_id.is_(None))  # type: ignore[union-attr]
            .where(
                Task.latest_macro_agent_run_id == Execution.id  # type: ignore[arg-type]
            )
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, execution in result.tuples().all():
            timeout = await self._task_timeout_minutes(task)
            deadline = self._as_utc(execution.started_at) + timedelta(
                minutes=timeout * self._crash_timeout_factor()
            )
            if now < deadline:
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "execution_id": execution.id,
                        "action": "would_fail_retry_start",
                        "reason": "retry execution start never completed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            retry_marker = await self._verification_retry_marker(
                task.id, task.execution_attempts
            )
            retry_detail: dict[str, Any] = {}
            if retry_marker is not None:
                retry_detail = {
                    "pending_event_id": retry_marker.event_id,
                    "attempt": task.execution_attempts,
                }
            await self._mark_failed(
                task,
                execution,
                "retry_execution_start_never_completed",
                detail=retry_detail,
            )
            actions.append(
                {
                    "task_id": task.id,
                    "execution_id": execution.id,
                    "action": "failed_retry_start",
                    "reason": "retry execution start never completed",
                    "deadline": deadline.isoformat(),
                }
            )
            await self.db.commit()

        return actions

    async def _verification_retry_marker(
        self, task_id: str, attempt: int
    ) -> AuditLog | None:
        """Return the pending marker for a logical verification retry attempt."""
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.task_id == task_id,  # type: ignore[arg-type]
                AuditLog.event_type == "verification_retry_pending",  # type: ignore[arg-type]
            )
            .order_by(AuditLog.__table__.c.id.desc())  # type: ignore[attr-defined]
        )
        for marker in result.scalars().all():
            if marker.payload.get("attempt") == attempt:
                return marker
        return None

    async def _poll_running(self) -> list[dict[str, Any]]:
        """Block RUNNING tasks whose execution has genuinely timed out."""
        tasks = await self._running_tasks_with_executions()
        if not tasks:
            return []

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, execution in tasks:
            timeout = await self._task_timeout_minutes(task)
            deadline = self._as_utc(execution.started_at) + timedelta(
                minutes=timeout * self._timeout_factor()
            )
            if now < deadline:
                continue

            alive, status_error = await self._execution_is_still_alive(
                execution,
                record_errors=not self._dry_run,
            )
            if alive:
                actions.append(
                    {
                        "task_id": task.id,
                        "execution_id": execution.id,
                        "action": "alive",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "execution_id": execution.id,
                        "action": "would_block",
                        "reason": (
                            "execution timed out without successful event delivery"
                        ),
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            if not await self._mark_blocked(
                task,
                execution,
                status_error=status_error,
            ):
                continue
            actions.append(
                {
                    "task_id": task.id,
                    "execution_id": execution.id,
                    "action": "blocked",
                    "reason": "execution timed out without successful event delivery",
                    "deadline": deadline.isoformat(),
                }
            )

            # Commit incrementally so a large backlog does not hold one
            # unbounded transaction open for the entire poll pass (#238).
            await self.db.commit()

        return actions

    async def _poll_ready(self) -> list[dict[str, Any]]:
        """Fail READY tasks whose executor.start() never completed (#253).

        A crash between the READY transition commit and the macro-agent /runs
        response leaves the task at READY with an Execution row in READY state
        and ``macro_agent_run_id`` still NULL. After a short grace window we
        treat this as a failed execution start so the task is not stranded
        forever.
        """
        result = await self.db.execute(
            select(Task, Execution)
            .join(Execution, Execution.task_id == Task.id)  # type: ignore[arg-type]
            .where(Task.state == TaskState.READY.value)  # type: ignore[arg-type]
            .where(Execution.state == TaskState.READY.value)  # type: ignore[arg-type]
            .where(Execution.macro_agent_run_id.is_(None))  # type: ignore[union-attr]
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, execution in result.tuples().all():
            timeout = await self._task_timeout_minutes(task)
            deadline = self._as_utc(execution.started_at) + timedelta(
                minutes=timeout * self._crash_timeout_factor()
            )
            if now < deadline:
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "execution_id": execution.id,
                        "action": "would_fail_ready",
                        "reason": "execution start never completed",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            await self._mark_failed(task, execution, "execution_start_never_completed")
            actions.append(
                {
                    "task_id": task.id,
                    "execution_id": execution.id,
                    "action": "failed_ready",
                    "reason": "execution start never completed",
                    "deadline": deadline.isoformat(),
                }
            )
            await self.db.commit()

        return actions

    async def _poll_agent_review(self) -> list[dict[str, Any]]:
        """Recover AGENT_REVIEW tasks abandoned by a crash during verification.

        The in-progress ProcessedEvent marker written by the EventBridge
        prevents duplicate verification runs, but after a Controller crash the
        marker remains forever and blocks legitimate redelivery (#254). If the
        marker is older than the verification timeout window and the task is
        still at AGENT_REVIEW, we move the task to BLOCKED with a human alert
        and delete the marker only when our CAS wins. A human must then send a
        ``conflict:resolved`` event (GAP-099) or manually intervene to unblock
        the task (#259).
        """
        latest_marker = aliased(ProcessedEvent)
        newer_marker_exists = exists().where(
            latest_marker.task_id == ProcessedEvent.task_id,  # type: ignore[arg-type]
            latest_marker.event_type == ProcessedEvent.event_type,  # type: ignore[arg-type]
            latest_marker.id > ProcessedEvent.id,  # type: ignore[operator, arg-type]
        )
        result = await self.db.execute(
            select(Task, ProcessedEvent)
            .join(
                ProcessedEvent,
                ProcessedEvent.task_id == Task.id,  # type: ignore[arg-type]
            )
            .where(Task.state == TaskState.AGENT_REVIEW.value)  # type: ignore[arg-type]
            .where(ProcessedEvent.event_type == "landing:completed")  # type: ignore[arg-type]
            .where(~newer_marker_exists)
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, marker in result.tuples().all():
            # Use a separate, independent budget for verification crash
            # detection rather than the task's execution timeout (#264).
            deadline = self._as_utc(marker.processed_at) + timedelta(
                minutes=self._verification_crash_timeout_minutes()
            )
            if now < deadline:
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "would_unblock_agent_review",
                        "reason": "verification marker stale after Controller crash",
                        "deadline": deadline.isoformat(),
                    }
                )
                continue

            success = await StateMachine.atomic_transition(
                self.db, task, TaskState.BLOCKED
            )
            if success:
                # Only delete the dedup marker when our own CAS won; otherwise a
                # concurrent successful verification would lose its idempotency
                # key (#263).
                await self.db.delete(marker)
                await AuditService.log(
                    db=self.db,
                    event_type="execution_blocked_timeout",
                    task_id=task.id,
                    actor="system:poller",
                    source="stuck_execution_poller",
                    payload={
                        "reason": "verification abandoned after Controller crash",
                        "processed_event_id": marker.event_id,
                    },
                )
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "unblocked_agent_review",
                        "reason": "verification marker stale after Controller crash",
                        "deadline": deadline.isoformat(),
                    }
                )
            await self.db.commit()

        return actions

    def _plane_projection_sweep_grace(self) -> timedelta:
        """Minimum age of a ``plane_projection_pending`` marker before sweeping."""
        return timedelta(minutes=5)

    async def _poll_plane_projection_pending(self) -> list[dict[str, Any]]:
        """Resolve orphaned Plane-projection markers (#314).

        A marker is orphaned when the Controller state has moved on (or already
        matches the projected state) but no ``plane_projection_completed``,
        ``plane_projection_failed``, or ``plane_projection_skipped`` sibling
        was ever recorded for it.  Only markers older than the grace window
        are touched, so an in-flight projection is not accidentally closed.
        """
        terminal = aliased(AuditLog)
        terminal_exists = exists().where(
            terminal.task_id == AuditLog.task_id,  # type: ignore[arg-type]
            terminal.payload["pending_event_id"].as_string()
            == AuditLog.event_id,
            terminal.event_type.in_(  # type: ignore[attr-defined]
                [
                    "plane_projection_completed",
                    "plane_projection_failed",
                    "plane_projection_skipped",
                ]
            ),
        )
        grace = self._plane_projection_sweep_grace()
        now = datetime.now(UTC)
        stmt = (
            select(AuditLog)
            .where(AuditLog.event_type == "plane_projection_pending")  # type: ignore[arg-type]
            .where(~terminal_exists)
            .order_by(AuditLog.__table__.c.id)  # type: ignore[attr-defined]
            .limit(self._batch_size)
        )
        if self._is_postgres() and not self._dry_run:
            stmt = stmt.with_for_update(of=AuditLog, skip_locked=True)
        result = await self.db.execute(stmt)

        actions: list[dict[str, Any]] = []
        markers = result.scalars().all()
        for marker in markers:
            if not self._dry_run:
                await acquire_plane_projection_event_lock(self.db, marker.event_id)
                terminal_result = await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.task_id == marker.task_id,  # type: ignore[arg-type]
                        AuditLog.__table__.c.event_type.in_(  # type: ignore[attr-defined]
                            [
                                "plane_projection_completed",
                                "plane_projection_failed",
                                "plane_projection_skipped",
                            ]
                        ),
                        AuditLog.payload["pending_event_id"].as_string()
                        == marker.event_id,
                    )
                    .limit(1)
                    .execution_options(populate_existing=True)
                )
                if terminal_result.scalar_one_or_none() is not None:
                    await self.db.commit()
                    continue

            if now - self._as_utc(marker.timestamp) < grace:
                if not self._dry_run:
                    await self.db.commit()
                continue
            task = await self.db.scalar(
                select(Task)
                .where(Task.id == marker.task_id)  # type: ignore[arg-type]
                .execution_options(populate_existing=True)
            )
            if task is None:
                if not self._dry_run:
                    await self.db.commit()
                continue

            operation = marker.payload.get("operation")
            resolved = False
            if operation == "update_state":
                # Reconciliation has independently moved Plane past the state
                # this marker was projecting; if the task is still exactly at
                # the pending state, leave the marker for normal completion.
                pending_state = marker.payload.get("state")
                resolved = (
                    pending_state is not None
                    and task.state.value != pending_state
                )
            elif operation == "terminal_failure_alert":
                # Terminal state is the condition that requires this alert; it
                # is not evidence that the external human notification arrived.
                resolved = False
            elif operation == "verification_failure_alert":
                resolved = task.state != TaskState.FAILED.value
            elif operation == "reconciliation_state_fix":
                pending_state = marker.payload.get("state")
                resolved = (
                    pending_state is not None
                    and task.state.value != pending_state
                )

            if not resolved:
                if not self._dry_run:
                    await self.db.commit()
                continue

            if self._dry_run:
                actions.append(
                    {
                        "task_id": task.id,
                        "action": "would_resolve_plane_projection_pending",
                        "pending_event_id": marker.event_id,
                    }
                )
                continue

            await AuditService.log(
                db=self.db,
                event_type="plane_projection_completed",
                task_id=task.id,
                actor="system",
                source="stuck_execution_poller",
                payload={
                    "operation": operation,
                    "pending_event_id": marker.event_id,
                    "reason": "resolved_independently",
                    "synthetic": True,
                },
            )
            await self.db.commit()
            actions.append(
                {
                    "task_id": task.id,
                    "action": "plane_projection_resolved",
                    "pending_event_id": marker.event_id,
                }
            )

        return actions

    async def _running_tasks_with_executions(
        self,
    ) -> list[tuple[Task, Execution]]:
        """Return a bounded batch of RUNNING tasks joined with their latest
        execution row.

        ``task.latest_macro_agent_run_id`` stores the external macro-agent run id
        returned by ``MacroAgentExecutor.start()``, not the internal
        ``Execution.id`` primary key. The join uses ``Execution.macro_agent_run_id``
        so real approval-created tasks match, and restricts the join to executions
        still in ``RUNNING`` state to avoid matching a stale pointer left by a
        retry in flight (#237).
        """
        # SQLModel/StrEnum mypy interaction: pass the string value and
        # ignore the false-positive bool-argument error.
        result = await self.db.execute(
            select(Task, Execution)
            .join(
                Execution,
                Execution.macro_agent_run_id == Task.latest_macro_agent_run_id,  # type: ignore[arg-type]
            )
            .where(Task.state == TaskState.RUNNING.value)  # type: ignore[arg-type]
            .where(Execution.state == TaskState.RUNNING.value)  # type: ignore[arg-type]
            .limit(self._batch_size)
            .execution_options(populate_existing=True)
        )
        pairs: list[tuple[Task, Execution]] = []
        for task, execution in result.tuples().all():
            pairs.append((task, execution))
        return pairs

    async def _task_timeout_minutes(self, task: Task) -> int:
        """Return the task's timeout_minutes, defaulting to project/profile."""
        contract = task.task_contract_json
        if isinstance(contract, dict):
            execution_section = contract.get("execution") or {}
            if isinstance(execution_section, dict):
                timeout = execution_section.get("timeout_minutes")
                if isinstance(timeout, int) and timeout > 0:
                    return timeout
        # Fall back to the project profile default.
        return 60

    async def _execution_is_still_alive(
        self,
        execution: Execution,
        record_errors: bool = True,
    ) -> tuple[bool, str | None]:
        """Return True if the macro-agent run is actively progressing.

        A status check that succeeds and reports a terminal state is treated as
        a delivery that handled the stuck detection itself; an exception or a
        non-terminal active state means we should block the task. Any diagnostic
        is returned for the caller to persist only after it wins the task CAS.
        """
        run_id = execution.macro_agent_run_id
        if not run_id:
            return False, None

        status: dict[str, Any] | None = None
        status_error: str | None = None
        try:
            client = self._client
            if client is None:
                executor = self._executor
                if executor is not None and hasattr(executor, "status"):
                    status = await executor.status(run_id)
                else:
                    status = await MacroAgentClient().status(run_id)
            else:
                status = await client.status(run_id)
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status_error = f"HTTPStatusError:{exc.response.status_code}"
            else:
                status_error = type(exc).__name__

        # The macro-agent service returns status under the key "status".
        run_status = status.get("status") if isinstance(status, dict) else None
        if run_status in {"running", "allocated", "active", "queued"}:
            return True, None
        # A genuine exception from the macro-agent service (timeout,
        # connection refused, 404 after a restart) is recorded so the audit
        # trail can distinguish a lost run from a merely slow one (#261).
        return False, status_error if record_errors else None


    async def _mark_blocked(
        self,
        task: Task,
        execution: Execution,
        reason_code: str = "execution_timed_out",
        detail: dict[str, Any] | None = None,
        status_error: str | None = None,
    ) -> bool:
        """Transition task to BLOCKED and record a human-alert audit entry."""
        success = await StateMachine.atomic_transition(self.db, task, TaskState.BLOCKED)
        if not success:
            return False

        execution.state = TaskState.BLOCKED
        execution.ended_at = datetime.now(UTC)
        if status_error is not None:
            execution.status_error = status_error
        await self.db.flush()

        payload: dict[str, Any] = {
            "reason": "execution timed out without successful event delivery",
            "reason_code": reason_code,
            "macro_agent_run_id": execution.macro_agent_run_id,
        }
        if detail:
            payload.update(detail)

        await AuditService.log(
            db=self.db,
            event_type="execution_blocked_timeout",
            task_id=task.id,
            actor="system:poller",
            source="stuck_execution_poller",
            execution_id=execution.id,
            payload=payload,
        )
        return True

    async def _mark_failed(
        self,
        task: Task,
        execution: Execution,
        reason_code: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Transition a READY/RUNNING execution and its task to FAILED."""
        success = await StateMachine.atomic_transition(self.db, task, TaskState.FAILED)
        if not success:
            return

        execution.state = TaskState.FAILED
        execution.ended_at = datetime.now(UTC)
        await self.db.flush()

        payload: dict[str, Any] = {
            "reason": "execution start never completed after READY transition",
            "reason_code": reason_code,
        }
        if detail:
            payload.update(detail)

        await AuditService.log(
            db=self.db,
            event_type="execution_start_failed",
            task_id=task.id,
            actor="system:poller",
            source="stuck_execution_poller",
            execution_id=execution.id,
            payload=payload,
        )
