"""Reconciliation service for Plane, Controller DB, and opentasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.constants import TaskState
from governance_controller.services.opentasks_materializer import (
    MaterializerError,
    OpentasksMaterializer,
)


@dataclass
class Divergence:
    """A single detected divergence between Plane and Controller."""

    plane_task_id: str
    controller_task_id: str | None
    field: str
    plane_value: Any
    controller_value: Any
    severity: str  # "alert" or "project"
    message: str


@dataclass
class ReconciliationReport:
    """Result of a reconciliation pass."""

    checked: int = 0
    divergences: list[Divergence] = field(default_factory=list)
    projection_fixes: list[tuple[str, str, Any]] = field(
        default_factory=list
    )


class ReconciliationService:
    """Compare Plane state with Controller state and report/fix divergences.

    Plane is a projection for execution-status fields, so the Controller
    always wins for status. Content drift on tasks that are already executing
    or beyond requires human attention; the service records an alert instead
    of overwriting.

    Args:
        plane_client: Optional PlaneClient override.
        materializer: Optional OpentasksMaterializer override.
    """

    def __init__(
        self,
        plane_client: PlaneClient | None = None,
        materializer: OpentasksMaterializer | None = None,
    ) -> None:
        self._client = plane_client
        self._materializer = materializer

    def _client_or_none(self) -> PlaneClient | None:
        if self._client is not None:
            return self._client
        if not settings.plane_base_url:
            return None
        return PlaneClient()

    async def reconcile(
        self,
        controller_tasks: list[tuple[str, TaskState, str]],
        project_id: str | None = None,
    ) -> ReconciliationReport:
        """Reconcile a list of Controller tasks against Plane.

        ``controller_tasks`` is a list of ``(task_id, state, project_id)``
        tuples. When ``project_id`` is not provided, the project ID from the
        first task is used.
        """
        client = self._client_or_none()
        report = ReconciliationReport()
        if client is None:
            return report

        if not controller_tasks:
            return report

        effective_project_id = (
            project_id or controller_tasks[0][2]
        )

        try:
            issues_response = await client.list_issues(
                project_id=effective_project_id,
                params={"page_size": 1000},
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to list Plane issues for reconciliation"
            ) from exc

        plane_issues = {
            issue.get("id", ""): issue
            for issue in _result_items(issues_response)
            if isinstance(issue, dict)
        }

        for task_id, state, _proj in controller_tasks:
            plane_issue = plane_issues.get(task_id)
            if plane_issue is None:
                report.divergences.append(
                    Divergence(
                        plane_task_id="",
                        controller_task_id=task_id,
                        field="presence",
                        plane_value=None,
                        controller_value=state.value,
                        severity="alert",
                        message=f"Controller task {task_id} not found in Plane",
                    )
                )
                continue

            plane_state = _issue_state_name(plane_issue)
            expected_plane = _controller_state_to_plane(state)
            if plane_state != expected_plane:
                report.divergences.append(
                    Divergence(
                        plane_task_id=plane_issue.get("id", ""),
                        controller_task_id=task_id,
                        field="state",
                        plane_value=plane_state,
                        controller_value=state.value,
                        severity="project",
                        message=(
                            f"Plane state '{plane_state}' does not match "
                            f"Controller state '{state.value}'"
                        ),
                    )
                )

            report.checked += 1

        # Validate runtime DAG for tasks in EXEC_APPROVED+ states that do
        # exist in Plane. Missing tasks are already reported above.
        for task_id, state, _proj in controller_tasks:
            if state.value in {"PROPOSED", "PLAN_APPROVED"}:
                continue
            if task_id not in plane_issues:
                continue
            try:
                await self._materializer_or_default().materialize(
                    root_plane_task_id=task_id,
                    project_id=effective_project_id,
                )
            except MaterializerError as exc:
                report.divergences.append(
                    Divergence(
                        plane_task_id=task_id,
                        controller_task_id=task_id,
                        field="dag",
                        plane_value=None,
                        controller_value=state.value,
                        severity="alert",
                        message=f"Runtime DAG validation failed: {exc}",
                    )
                )

        return report

    def _materializer_or_default(self) -> OpentasksMaterializer:
        return self._materializer or OpentasksMaterializer(
            client=self._client_or_none()
        )


def _result_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("results", result)
    if isinstance(items, list):
        return items
    return []


def _issue_state_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return state.get("name") or ""
    return str(state) if state is not None else ""


def _controller_state_to_plane(state: TaskState) -> str:
    mapping: dict[TaskState, str] = {
        TaskState.PROPOSED: "Proposed",
        TaskState.PLAN_APPROVED: "Plan Approved",
        TaskState.EXEC_APPROVED: "Approved",
        TaskState.READY: "Ready",
        TaskState.RUNNING: "In Progress",
        TaskState.AGENT_REVIEW: "Agent Review",
        TaskState.HUMAN_REVIEW: "In Review",
        TaskState.DONE: "Done",
        TaskState.FAILED: "Failed",
        TaskState.BLOCKED: "Blocked",
    }
    return mapping.get(state, "")
