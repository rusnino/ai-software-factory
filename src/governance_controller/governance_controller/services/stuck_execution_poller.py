"""Fallback poller for stuck macro-agent executions.

SPEC-05 §5.6: if the Event Bridge stops delivering events, the Controller must
poll the macro-agent service for execution status and transition tasks that
have exceeded their timeout budget to BLOCKED with a human-alert audit entry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.services.audit_service import AuditService
from governance_controller.services.state_machine import StateMachine


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

    def _timeout_factor(self) -> int:
        return 2

    async def poll(self) -> list[dict[str, Any]]:
        """Run one polling pass and return a list of actions taken."""
        tasks = await self._running_tasks_with_executions()
        if not tasks:
            return []

        now = datetime.now(UTC)
        actions: list[dict[str, Any]] = []
        for task, execution in tasks:
            timeout = await self._task_timeout_minutes(task)
            deadline = execution.started_at + timedelta(
                minutes=timeout * self._timeout_factor()
            )
            if now < deadline:
                continue

            if await self._execution_is_still_alive(execution):
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

            await self._mark_blocked(task, execution)
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

    async def _execution_is_still_alive(self, execution: Execution) -> bool:
        """Return True if the macro-agent run is actively progressing.

        A status check that succeeds and reports a terminal state is treated as
        a delivery that handled the stuck detection itself; an exception or a
        non-terminal active state means we should block the task.
        """
        run_id = execution.macro_agent_run_id
        if not run_id:
            return False

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
        except Exception:
            return False

        # The macro-agent service returns status under the key "status".
        run_status = status.get("status") if isinstance(status, dict) else None
        return run_status in {"running", "allocated", "active", "queued"}

    async def _mark_blocked(self, task: Task, execution: Execution) -> None:
        """Transition task to BLOCKED and record a human-alert audit entry."""
        success = await StateMachine.atomic_transition(self.db, task, TaskState.BLOCKED)
        if not success:
            return

        execution.state = TaskState.BLOCKED
        execution.ended_at = datetime.now(UTC)
        await self.db.flush()

        await AuditService.log(
            db=self.db,
            event_type="execution_blocked_timeout",
            task_id=task.id,
            actor="system:poller",
            source="stuck_execution_poller",
            execution_id=execution.id,
            payload={
                "reason": "execution timed out without successful event delivery",
                "macro_agent_run_id": execution.macro_agent_run_id,
            },
        )
