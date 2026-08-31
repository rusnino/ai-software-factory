"""Tests for the stuck-execution fallback poller (SPEC-05 §5.6)."""

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task
from governance_controller.services.state_machine import StateMachine
from governance_controller.services.stuck_execution_poller import StuckExecutionPoller


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


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

        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == _execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error == "RuntimeError"

    @pytest.mark.parametrize("status_code", [404, 500])
    async def test_status_http_error_persists_status_code(
        self,
        db_session: AsyncSession,
        httpx_mock: pytest_httpx.HTTPXMock,
        status_code: int,
    ) -> None:
        """#286: 404 and 500 status failures remain distinguishable."""
        task, execution = await _running_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=300),
            macro_agent_run_id=f"run-http-{status_code}",
        )
        httpx_mock.add_response(status_code=status_code)

        poller = StuckExecutionPoller(
            db_session,
            client=MacroAgentClient(base_url="https://example.com"),
        )
        actions = await poller.poll()

        assert len(actions) == 1
        assert actions[0]["action"] == "blocked"
        refreshed = await db_session.scalar(
            select(Execution).where(Execution.id == execution.id)
        )
        assert refreshed is not None
        assert refreshed.status_error == f"HTTPStatusError:{status_code}"

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


async def _ready_task_with_execution(
    db_session: Any,
    started_at: datetime,
    timeout_minutes: int = 60,
) -> tuple[Task, Execution]:
    internal_id = str(uuid4())
    task = Task(
        id=f"task-ready-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.READY,
        task_contract_json={
            "execution": {"timeout_minutes": timeout_minutes},
        },
    )
    execution = Execution(
        id=internal_id,
        task_id=task.id,
        state=TaskState.READY,
        started_at=started_at,
        macro_agent_run_id=None,
    )
    db_session.add(task)
    db_session.add(execution)
    await db_session.flush()
    return task, execution


async def _agent_review_task_with_marker(
    db_session: Any,
    processed_at: datetime,
) -> tuple[Task, ProcessedEvent]:
    internal_id = str(uuid4())
    task = Task(
        id=f"task-review-{internal_id[:8]}",
        project_id="proj-1",
        proposed_by="agent-1",
        state=TaskState.AGENT_REVIEW,
        task_contract_json={"execution": {"timeout_minutes": 60}},
    )
    marker = ProcessedEvent(
        task_id=task.id,
        event_type="landing:completed",
        event_timestamp=datetime.now(UTC),
        event_id=f"evt-{internal_id[:8]}",
        processed_at=processed_at,
    )
    db_session.add(task)
    db_session.add(marker)
    await db_session.flush()
    return task, marker


class TestPollReady:
    """#253: READY tasks whose executor.start() never completed."""

    async def test_execution_start_never_completed_fails_after_deadline(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, execution = await _ready_task_with_execution(
            db_session,
            # timeout_minutes=1, crash factor 2x -> 2 minute deadline.
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            timeout_minutes=1,
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        ready_actions = [a for a in actions if a["action"] == "failed_ready"]
        assert len(ready_actions) == 1
        assert ready_actions[0]["task_id"] == task.id
        assert ready_actions[0]["execution_id"] == execution.id

        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.FAILED

        result = await db_session.execute(
            select(Execution).where(Execution.id == execution.id)
        )
        refreshed_execution = result.scalar_one()
        assert refreshed_execution.state == TaskState.FAILED
        assert refreshed_execution.ended_at is not None

    async def test_execution_within_grace_window_is_not_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, _execution = await _ready_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(seconds=5),
            timeout_minutes=60,
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [a for a in actions if a["action"] == "failed_ready"] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.READY

    async def test_execution_with_run_id_is_not_touched(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A READY execution that already got a real run id is not #253's case."""
        task, execution = await _ready_task_with_execution(
            db_session,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            timeout_minutes=1,
        )
        execution.macro_agent_run_id = "run-already-started"
        await db_session.flush()

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [a for a in actions if a["action"] == "failed_ready"] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.READY


class TestPollAgentReview:
    """#254: AGENT_REVIEW tasks abandoned by a crash during verification."""

    async def test_stale_marker_transitions_to_blocked_and_deletes_marker(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, marker = await _agent_review_task_with_marker(
            db_session,
            # Fixed 15-minute verification-crash budget (#264); well past it.
            processed_at=datetime.now(UTC) - timedelta(minutes=30),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        review_actions = [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ]
        assert len(review_actions) == 1
        assert review_actions[0]["task_id"] == task.id

        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.BLOCKED

        result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.id == marker.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_marker_within_grace_window_is_not_touched(
        self,
        db_session: AsyncSession,
    ) -> None:
        task, marker = await _agent_review_task_with_marker(
            db_session,
            processed_at=datetime.now(UTC) - timedelta(minutes=2),
        )

        poller = StuckExecutionPoller(db_session)
        actions = await poller.poll()

        assert [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ] == []
        state = await _fetch_task_state(db_session, task.id)
        assert state == TaskState.AGENT_REVIEW

        result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.id == marker.id)
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.skipif(
        not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
        reason="aiosqlite returns tz-naive datetimes for this fixture's "
        "engine setup, which the poller's tz-aware comparison rejects; "
        "the CAS-loss semantics under test require real Postgres anyway",
    )
    async def test_marker_survives_when_blocked_cas_genuinely_loses(
        self,
        isolated_db: tuple,
    ) -> None:
        """#263: a concurrent winner must not have its dedup marker deleted.

        Genuinely mutates the task's state/version from a SEPARATE session
        between the poller's own SELECT and its ``atomic_transition`` call, so
        the poller's CAS to BLOCKED loses for real (version/state mismatch),
        not via a mocked return value. The marker — which a legitimately
        successful concurrent event delivery may still need — must survive.
        """
        engine, local_session = isolated_db

        async with local_session() as seed:
            task, marker = await _agent_review_task_with_marker(
                seed, processed_at=datetime.now(UTC) - timedelta(minutes=30)
            )
            task_id = task.id
            marker_id = marker.id
            await seed.commit()

        original_atomic_transition = StateMachine.atomic_transition

        async def _race_then_attempt(db, task, target_state):  # type: ignore[no-untyped-def]
            if target_state == TaskState.BLOCKED:
                async with local_session() as racer:
                    await racer.execute(
                        update(Task)
                        .where(Task.id == task_id)
                        .values(state=TaskState.FAILED.value, version=Task.version + 1)
                    )
                    await racer.commit()
            return await original_atomic_transition(db, task, target_state)

        async with local_session() as poller_session:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(StateMachine, "atomic_transition", _race_then_attempt)
                poller = StuckExecutionPoller(poller_session)
                actions = await poller.poll()

        assert [
            a for a in actions if a["action"] == "unblocked_agent_review"
        ] == []

        async with local_session() as check:
            result = await check.execute(select(Task).where(Task.id == task_id))
            task_row = result.scalar_one()
            assert task_row.state == TaskState.FAILED  # the real winner

            marker_result = await check.execute(
                select(ProcessedEvent).where(ProcessedEvent.id == marker_id)
            )
            assert marker_result.scalar_one_or_none() is not None, (
                "the loser's failed CAS must not delete the marker a "
                "concurrent winner may still depend on"
            )


class _FakeMacroAgentClient:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self._statuses = statuses or {}

    async def status(self, run_id: str) -> dict[str, Any]:
        return {"status": self._statuses.get(run_id, "completed")}


class _FailingMacroAgentClient:
    async def status(self, run_id: str) -> dict[str, Any]:
        raise RuntimeError("macro-agent unreachable")
