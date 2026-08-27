"""Tests for the stuck-execution fallback poller (SPEC-05 §5.6)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task
from governance_controller.services.stuck_execution_poller import StuckExecutionPoller


async def _running_task_with_execution(
    db_session: Any,
    started_at: datetime,
    timeout_minutes: int = 60,
    macro_agent_run_id: str | None = None,
) -> tuple[Task, Execution]:
    internal_id = str(uuid4())
    external_run_id = macro_agent_run_id or f"run-{internal_id[:8]}"
    task = Task(
        id=f"task-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.RUNNING,
        # Store the external macro-agent run id, matching ApprovalService wiring.
        latest_macro_agent_run_id=external_run_id,
        task_contract_json={
            "execution": {"timeout_minutes": timeout_minutes},
        },
    )
    execution = Execution(
        id=internal_id,
        task_id=task.id,
        state=TaskState.RUNNING,
        started_at=started_at,
        macro_agent_run_id=external_run_id,
    )
    db_session.add(task)
    db_session.add(execution)
    await db_session.flush()
    return task, execution


async def _fetch_task_state(db_session: AsyncSession, task_id: str) -> TaskState:
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    return task.state


class TestStuckExecutionPoller:
    async def test_recent_execution_is_not_blocked(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert actions == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING

    async def test_stuck_execution_transitions_to_blocked(
        self,
        db_session: Any,
    ) -> None:
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-stuck",
        )

        poller = StuckExecutionPoller(
            db_session,
            client=_FakeMacroAgentClient(),
        )
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        assert actions[0]["task_id"] == task.id
        assert actions[0]["execution_id"] == execution.id
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

    async def test_still_active_execution_stays_running(
        self,
        db_session: Any,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-active",
        )

        client = _FakeMacroAgentClient({"run-active": "running"})
        poller = StuckExecutionPoller(db_session, client=client)
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "alive"
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING

    async def test_status_exception_treats_as_not_alive(
        self,
        db_session: Any,
    ) -> None:
        task, _execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id="run-error",
        )

        client = _FailingMacroAgentClient()
        poller = StuckExecutionPoller(db_session, client=client)
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

    async def test_poller_ignores_stale_latest_run_id_during_retry_window(
        self,
        db_session: Any,
    ) -> None:
        """#237: a stale latest_macro_agent_run_id pointing at an old FAILED
        execution must not cause the poller to block a task whose real, current
        retry execution is still RUNNING without an external run id yet.
        """
        internal_id = str(uuid4())
        task = Task(
            id=f"task-retry-race-{internal_id[:8]}",
            project_id="proj-1",
            proposed_by="agent-1",
            state=TaskState.RUNNING,
            latest_macro_agent_run_id="old-run-id",
            task_contract_json={"execution": {"timeout_minutes": 60}},
        )
        old_execution = Execution(
            id=str(uuid4()),
            task_id=task.id,
            state=TaskState.FAILED,
            started_at=datetime.now(UTC) - timedelta(hours=5),
            ended_at=datetime.now(UTC) - timedelta(hours=4),
            macro_agent_run_id="old-run-id",
        )
        new_execution = Execution(
            id=internal_id,
            task_id=task.id,
            state=TaskState.RUNNING,
            started_at=datetime.now(UTC),
            macro_agent_run_id=None,
        )
        db_session.add(task)
        db_session.add(old_execution)
        db_session.add(new_execution)
        await db_session.flush()

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert actions == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.RUNNING


class _FakeMacroAgentClient:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self._statuses = statuses or {}

    async def status(self, run_id: str) -> dict[str, Any]:
        return {"status": self._statuses.get(run_id, "completed")}


class _FailingMacroAgentClient:
    async def status(self, run_id: str) -> dict[str, Any]:
        raise RuntimeError("macro-agent unreachable")
