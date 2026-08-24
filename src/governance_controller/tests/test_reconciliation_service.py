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
        page_size: int = 1000,
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
