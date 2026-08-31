"""Tests for the reconciliation service."""

import asyncio
import os
from typing import Any

import pytest
from sqlalchemy import select

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.task import Task
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.reconciliation_service import (
    ReconciliationReport,
    ReconciliationService,
)


class _FakePlaneClient:
    def __init__(
        self,
        issues: list[dict[str, Any]] | None = None,
        dependencies: dict[str, list[str]] | None = None,
    ) -> None:
        self.issues = issues or []
        self.dependencies = dependencies or {}

    async def list_issues(
        self,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"results": self.issues}

    async def get_issue(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        for issue in self.issues:
            if issue.get("id") == issue_id:
                return issue
        raise RuntimeError(f"Issue {issue_id} not found")

    async def list_issue_dependencies(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        deps = self.dependencies.get(issue_id, [])
        return {"results": [{"related_issue": {"id": dep}} for dep in deps]}

    async def list_all_issues(
        self,
        project_id: str | None = None,
        per_page: int = 1000,
    ) -> dict[str, Any]:
        return {"results": self.issues}

    async def list_states(
        self,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "results": [
                {"id": "state-in-progress", "name": "In Progress"},
                {"id": "state-approved", "name": "Approved"},
            ]
        }


@pytest.fixture
def fake_client() -> _FakePlaneClient:
    return _FakePlaneClient(
        issues=[
            {
                "id": "plane-issue-1",
                "name": "Approved task",
                "state": {"name": "In Progress"},
            },
            {
                "id": "plane-issue-2",
                "name": "Human review task",
                "state": {"name": "Done"},
            },
            {
                "id": "plane-issue-3",
                "name": "Leaf task",
                "state": {"name": "Approved"},
            },
        ],
        dependencies={
            "plane-issue-1": ["plane-issue-3"],
            "plane-issue-2": [],
            "plane-issue-3": [],
        },
    )


async def test_no_divergence_when_states_match(
    fake_client: _FakePlaneClient,
) -> None:
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            ("controller-task-1", TaskState.RUNNING, "proj-1", "plane-issue-1"),
            (
                "controller-task-3",
                TaskState.EXEC_APPROVED,
                "proj-1",
                "plane-issue-3",
            ),
        ],
        project_id="proj-1",
    )

    assert report.checked == 2
    assert not report.divergences


async def test_missing_plane_issue_reported(
    fake_client: _FakePlaneClient,
) -> None:
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            ("controller-missing", TaskState.RUNNING, "proj-1", "plane-missing"),
        ],
        project_id="proj-1",
    )

    assert len(report.divergences) == 1
    assert report.divergences[0].field == "presence"
    assert report.divergences[0].severity == "alert"


async def test_state_mismatch_reported(
    fake_client: _FakePlaneClient,
) -> None:
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            (
                "controller-task-2",
                TaskState.HUMAN_REVIEW,
                "proj-1",
                "plane-issue-2",
            ),
        ],
        project_id="proj-1",
    )

    divs = [d for d in report.divergences if d.field == "state"]
    assert len(divs) == 1
    assert divs[0].plane_value == "Done"
    assert divs[0].controller_value == "HUMAN_REVIEW"
    assert divs[0].severity == "project"


async def test_state_uuid_resolved_to_name(
    fake_client: _FakePlaneClient,
) -> None:
    # Plane sometimes returns a state UUID instead of a state name dict.
    fake_client.issues = [
        {
            "id": "plane-issue-uuid",
            "name": "Task with UUID state",
            "state": "state-in-progress",
        }
    ]
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            (
                "controller-uuid-state",
                TaskState.RUNNING,
                "proj-1",
                "plane-issue-uuid",
            )
        ],
        project_id="proj-1",
    )

    assert report.checked == 1
    assert not any(d.field == "state" for d in report.divergences)


async def test_dag_validation_failure_reported(
    fake_client: _FakePlaneClient,
) -> None:
    fake_client.dependencies["plane-issue-1"] = ["plane-missing"]
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            ("controller-task-1", TaskState.RUNNING, "proj-1", "plane-issue-1"),
        ],
        project_id="proj-1",
    )

    divs = [d for d in report.divergences if d.field == "dag"]
    assert len(divs) == 1
    assert divs[0].severity == "alert"


async def test_without_plane_config_returns_empty_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    service = ReconciliationService()
    report = await service.reconcile(
        controller_tasks=[
            ("controller-task-1", TaskState.RUNNING, "proj-1", "plane-issue-1")
        ],
        project_id="proj-1",
    )

    assert report.checked == 0
    assert not report.divergences


async def test_matches_plane_issue_id_not_controller_task_id(
    fake_client: _FakePlaneClient,
) -> None:
    """#282: reconcile uses Plane's issue id when it differs from Task.id."""
    fake_client.issues = [
        {
            "id": "plane-issue-uuid-1",
            "name": "Controller task",
            "state": {"name": "In Progress"},
        }
    ]
    service = ReconciliationService(plane_client=fake_client)

    report = await service.reconcile(
        controller_tasks=[
            (
                "controller-task-1",
                TaskState.RUNNING,
                "proj-1",
                "plane-issue-uuid-1",
            )
        ],
        project_id="proj-1",
    )

    assert report.checked == 1
    assert not any(d.field == "presence" for d in report.divergences)
    assert not any(d.field == "state" for d in report.divergences)


async def test_task_still_in_state_detects_concurrent_change(
    isolated_db: tuple,
) -> None:
    """#275: the staleness guard must see a genuinely concurrent state change.

    Mirrors the CLI's real usage shape: the same session that already loaded
    a task (e.g. via the ``reconcile`` command's initial project-wide read)
    is reused for the staleness check. Without ``populate_existing=True``,
    SQLAlchemy's identity map would silently return the session's
    already-loaded, stale object instead of querying the database, and the
    guard would wrongly report the task as "still RUNNING" after a genuinely
    concurrent session moved it to ``HUMAN_REVIEW`` and committed.
    """
    from sqlalchemy import select

    engine, local_session = isolated_db

    async with local_session() as seed:
        task = Task(
            id="task-reconcile-staleness-275",
            project_id="proj-1",
            proposed_by="agent-1",
            state=TaskState.RUNNING,
        )
        seed.add(task)
        await seed.commit()

    session_a = local_session()
    try:
        # Session A loads the task once, identity-mapping it — exactly like
        # the CLI's initial project-wide `select(Task)` before reconciliation.
        loaded = await session_a.scalar(
            select(Task).where(Task.id == "task-reconcile-staleness-275")
        )
        assert loaded is not None
        assert loaded.state == TaskState.RUNNING

        # A genuinely separate session commits a real concurrent transition.
        async with local_session() as session_b:
            task_b = await session_b.scalar(
                select(Task).where(Task.id == "task-reconcile-staleness-275")
            )
            assert task_b is not None
            task_b.state = TaskState.HUMAN_REVIEW
            await session_b.commit()

        service = ReconciliationService(db=session_a)
        still_running = await service._task_still_in_state(
            "task-reconcile-staleness-275", TaskState.RUNNING
        )
        assert still_running is False, (
            "the guard must detect the concurrent change to HUMAN_REVIEW, "
            "not report the session's stale in-memory RUNNING copy"
        )

        still_human_review = await service._task_still_in_state(
            "task-reconcile-staleness-275", TaskState.HUMAN_REVIEW
        )
        assert still_human_review is True
    finally:
        await session_a.close()


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_reconciliation_projection_cannot_overwrite_newer_state(
    isolated_db: tuple,
) -> None:
    """#287: reconciliation and approvals share task projection ordering."""

    class _OrderedProjection:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.states: list[TaskState] = []

        async def update_state(self, **kwargs: object) -> dict[str, object]:
            state = kwargs["state"]
            assert isinstance(state, TaskState)
            if state == TaskState.RUNNING:
                self.started.set()
                await self.release.wait()
            self.states.append(state)
            return {}

        async def add_comment(self, **_kwargs: object) -> dict[str, object]:
            return {}

    _engine, local_session = isolated_db
    projection = _OrderedProjection()
    task_id = "task-reconcile-order-287"

    async with local_session() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id="proj-1",
                state=TaskState.RUNNING,
                version=0,
                proposed_by="agent-1",
            )
        )
        await seed.commit()

    session_a = local_session()
    reconciliation_task = None
    approval_task = None
    try:
        reconciliation = ReconciliationService(
            projection_service=projection,  # type: ignore[arg-type]
            db=session_a,
        )
        reconciliation_task = asyncio.create_task(
            reconciliation._apply_state_fix(
                projection=projection,  # type: ignore[arg-type]
                plane_issue_id="plane-issue-287",
                controller_task_id=task_id,
                state=TaskState.RUNNING,
                expected_plane="In Progress",
                report=ReconciliationReport(),
                project_id="proj-1",
            )
        )
        await projection.started.wait()

        async def _project_newer_state() -> None:
            async with local_session() as session_b:
                task_b = await session_b.scalar(select(Task).where(Task.id == task_id))
                assert task_b is not None
                task_b.state = TaskState.HUMAN_REVIEW
                task_b.version += 1
                await session_b.commit()
                await ApprovalService(
                    db=session_b,
                    plane_projection=projection,  # type: ignore[arg-type]
                )._project_state_to_plane(
                    task=task_b,
                    state=TaskState.HUMAN_REVIEW,
                    approval_type=ApprovalType.MERGE,
                )

        approval_task = asyncio.create_task(_project_newer_state())
        for _ in range(100):
            async with local_session() as observer:
                current = await observer.scalar(select(Task).where(Task.id == task_id))
            if current is not None and current.state == TaskState.HUMAN_REVIEW:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("newer Controller state was not committed")

        projection.release.set()
        await reconciliation_task
        await approval_task
        assert projection.states[-1] == TaskState.HUMAN_REVIEW
    finally:
        projection.release.set()
        if reconciliation_task is not None and not reconciliation_task.done():
            await reconciliation_task
        if approval_task is not None and not approval_task.done():
            await approval_task
        await session_a.close()
