"""Reconciliation service for Plane, Controller DB, and opentasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.constants import TaskState
from governance_controller.services.opentasks_materializer import (
    MaterializerError,
    OpentasksMaterializer,
)
from governance_controller.services.plane_projection import (
    PlaneProjectionService,
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
    opentasks_ids: dict[str, str] = field(default_factory=dict)


class ReconciliationError(Exception):
    """Raised when a reconciliation fix cannot be applied."""


class ReconciliationService:
    """Compare Plane state with Controller state and report/fix divergences.

    Plane is a projection for execution-status fields, so the Controller
    always wins for status. State mismatches are corrected when ``fix=True``;
    missing Plane issues and DAG validation failures remain ``alert``
    divergences requiring human attention.

    Args:
        plane_client: Optional PlaneClient override.
        materializer: Optional OpentasksMaterializer override.
        projection_service: Optional PlaneProjectionService override.
    """

    def __init__(
        self,
        plane_client: PlaneClient | None = None,
        materializer: OpentasksMaterializer | None = None,
        projection_service: PlaneProjectionService | None = None,
        db: AsyncSession | None = None,
    ) -> None:
        self._client = plane_client
        self._materializer = materializer
        self._projection = projection_service
        self._db = db

    def _client_or_none(self) -> PlaneClient | None:
        if self._client is not None:
            return self._client
        if not settings.plane_base_url:
            return None
        return PlaneClient()

    def _projection_service(self) -> PlaneProjectionService | None:
        if self._projection is not None:
            return self._projection
        client = self._client_or_none()
        if client is None:
            return None
        return PlaneProjectionService(client=client)

    async def reconcile(
        self,
        controller_tasks: list[tuple[str, TaskState, str]],
        project_id: str | None = None,
        dry_run: bool = False,
        fix: bool = False,
    ) -> ReconciliationReport:
        """Reconcile a list of Controller tasks against Plane.

        ``controller_tasks`` is a list of ``(task_id, state, project_id)``
        tuples. When ``project_id`` is not provided, the project ID from the
        first task is used.

        When ``fix`` is True, the service attempts to correct Plane state for
        ``project``-severity divergences and writes an explanatory comment. It
        never creates missing Plane issues or overwrites content on Plane; those
        remain ``alert`` divergences requiring human attention.
        """
        client = self._client_or_none()
        report = ReconciliationReport()
        if client is None:
            return report

        if not controller_tasks:
            return report

        projection = self._projection_service()
        effective_project_id = (
            project_id or controller_tasks[0][2]
        )

        try:
            issues_response = await client.list_all_issues(
                project_id=effective_project_id,
                per_page=1000,
            )
            states_response = await client.list_states(
                project_id=effective_project_id
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to list Plane issues for reconciliation"
            ) from exc

        state_names = _build_state_name_map(states_response)
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

            plane_state = _issue_state_name(plane_issue, state_names)
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
                if fix and projection is not None and not dry_run:
                    try:
                        await self._apply_state_fix(
                            projection=projection,
                            plane_issue_id=plane_issue.get("id", ""),
                            controller_task_id=task_id,
                            state=state,
                            expected_plane=expected_plane,
                            report=report,
                            opentasks_id=report.opentasks_ids.get(task_id),
                        )
                    except ReconciliationError as exc:
                        report.divergences.append(
                            Divergence(
                                plane_task_id=plane_issue.get("id", ""),
                                controller_task_id=task_id,
                                field="state",
                                plane_value=plane_state,
                                controller_value=state.value,
                                severity="alert",
                                message=str(exc),
                            )
                        )

            report.checked += 1

        # Validate runtime DAG for tasks in EXEC_APPROVED+ states that do
        # exist in Plane. Missing tasks are already reported above.
        opentasks_ids: dict[str, str] = {}
        for task_id, state, _proj in controller_tasks:
            if state.value in {"PROPOSED", "PLAN_APPROVED"}:
                continue
            if task_id not in plane_issues:
                continue
            try:
                dag = await self._materializer_or_default().materialize(
                    root_plane_task_id=task_id,
                    project_id=effective_project_id,
                )
                if dag.tasks:
                    opentasks_ids[task_id] = dag.tasks[0].id
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

        report.opentasks_ids = opentasks_ids
        return report

    async def _apply_state_fix(
        self,
        projection: PlaneProjectionService,
        plane_issue_id: str,
        controller_task_id: str,
        state: TaskState,
        expected_plane: str,
        report: ReconciliationReport,
        opentasks_id: str | None = None,
    ) -> None:
        """Update Plane state to match Controller and record the fix.

        Before applying the fix, re-read the Controller task from the database
        to ensure its state has not changed since the divergence was detected.
        This prevents a stale reconciliation pass from overwriting Plane with
        an out-of-date state under a race.
        """
        try:
            if self._db is not None and not await self._task_still_in_state(
                controller_task_id, state
            ):
                report.divergences.append(
                    Divergence(
                        plane_task_id=plane_issue_id,
                        controller_task_id=controller_task_id,
                        field="state",
                        plane_value=None,
                        controller_value=state.value,
                        severity="alert",
                        message=(
                            f"Controller task {controller_task_id} state "
                            f"changed during reconciliation; fix skipped"
                        ),
                    )
                )
                return

            updated = await projection.update_state(
                controller_task_id=controller_task_id,
                plane_issue_id=plane_issue_id,
                state=state,
                opentasks_id=opentasks_id,
            )
            if updated is None:
                return
            report.projection_fixes.append(
                (
                    controller_task_id,
                    plane_issue_id,
                    {"state": state.value},
                )
            )
            await projection.add_comment(
                plane_issue_id=plane_issue_id,
                text=(
                    "Reconciliation: Plane state was adjusted to match the "
                    f"authoritative Controller state '{state.value}' "
                    f"(expected Plane state '{expected_plane}')."
                ),
            )
        except Exception as exc:
            raise ReconciliationError(
                f"Failed to apply Plane state fix for {controller_task_id}"
            ) from exc

    async def _task_still_in_state(
        self, task_id: str, state: TaskState
    ) -> bool:
        """Return True if the Controller task still has ``state``."""
        if self._db is None:
            return True
        from governance_controller.models import Task

        result = await self._db.execute(
            select(Task).where(Task.id == task_id)  # type: ignore[arg-type]
        )
        task_obj = result.scalar_one_or_none()
        return task_obj is not None and task_obj.state == state.value

    def _materializer_or_default(self) -> OpentasksMaterializer:
        return self._materializer or OpentasksMaterializer(
            client=self._client_or_none()
        )


def _result_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("results", result)
    if isinstance(items, list):
        return items
    return []


def _issue_state_name(
    issue: dict[str, Any], state_name_map: dict[str, str]
) -> str:
    """Return the human-readable Plane state name for an issue.

    Plane issues may carry either a state UUID string or a state dict. UUIDs
    are resolved against ``state_name_map`` built from ``list_states``.
    """
    state = issue.get("state")
    state_id: str | None = None
    if isinstance(state, dict):
        return state.get("name") or ""
    if isinstance(state, str):
        state_id = state
    return state_name_map.get(state_id or "", state_id or "")


def _build_state_name_map(states_response: dict[str, Any]) -> dict[str, str]:
    """Build a {state_uuid: state_name} map from Plane list_states response."""
    result: dict[str, str] = {}
    for item in _result_items(states_response):
        if not isinstance(item, dict):
            continue
        state_id = item.get("id")
        name = item.get("name")
        if isinstance(state_id, str) and isinstance(name, str):
            result[state_id] = name
    return result


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
