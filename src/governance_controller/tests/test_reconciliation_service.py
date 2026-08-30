"""Tests for the reconciliation service."""

from typing import Any

import pytest

from governance_controller.constants import TaskState
from governance_controller.services.reconciliation_service import (
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
                "id": "P-1",
                "name": "Approved task",
                "state": {"name": "In Progress"},
            },
            {
                "id": "P-2",
                "name": "Human review task",
                "state": {"name": "Done"},
            },
            {
                "id": "P-3",
                "name": "Leaf task",
                "state": {"name": "Approved"},
            },
        ],
        dependencies={
            "P-1": ["P-3"],
            "P-2": [],
            "P-3": [],
        },
    )


async def test_no_divergence_when_states_match(
    fake_client: _FakePlaneClient,
) -> None:
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            ("P-1", TaskState.RUNNING, "proj-1"),
            ("P-3", TaskState.EXEC_APPROVED, "proj-1"),
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
            ("P-missing", TaskState.RUNNING, "proj-1"),
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
            ("P-2", TaskState.HUMAN_REVIEW, "proj-1"),
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
            "id": "P-uuid-state",
            "name": "Task with UUID state",
            "state": "state-in-progress",
        }
    ]
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[("P-uuid-state", TaskState.RUNNING, "proj-1")],
        project_id="proj-1",
    )

    assert report.checked == 1
    assert not any(d.field == "state" for d in report.divergences)


async def test_dag_validation_failure_reported(
    fake_client: _FakePlaneClient,
) -> None:
    fake_client.dependencies["P-1"] = ["P-missing"]
    service = ReconciliationService(plane_client=fake_client)
    report = await service.reconcile(
        controller_tasks=[
            ("P-1", TaskState.RUNNING, "proj-1"),
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
        controller_tasks=[("P-1", TaskState.RUNNING, "proj-1")],
        project_id="proj-1",
    )

    assert report.checked == 0
    assert not report.divergences


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

    from governance_controller.models.task import Task

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
