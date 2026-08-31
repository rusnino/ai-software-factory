"""Controller → Plane CE projection service.

Keeps Plane issue state, comments, and dependencies in sync with the
authoritative Controller state. Plane is a projection; the Controller decides
when and how to update it.
"""

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.constants import TaskState


def _plane_projection_lock_key(controller_task_id: str) -> int:
    """Return a stable advisory-lock key for one Controller task."""
    digest = hashlib.sha256(
        b"plane-projection:" + controller_task_id.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def acquire_plane_projection_lock(
    db: AsyncSession, controller_task_id: str
) -> None:
    """Serialize Plane projections for one task on PostgreSQL.

    The caller must commit or roll back after the external projection completes.
    This is deliberately a task-scoped transaction lock, not the global audit
    chain lock, so it cannot serialize unrelated tasks across Plane I/O.
    """
    bind = db.bind
    if bind is None or bind.dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _plane_projection_lock_key(controller_task_id)},
    )

# Mapping from Controller state name to Plane state display name. Plane CE state
# UUIDs are fetched at runtime per project.
_CONTROLLER_TO_PLANE_STATE_NAME: dict[TaskState, str] = {
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


class PlaneProjectionService:
    """Project Controller task state to Plane CE.

    Args:
        client: Optional PlaneClient override. Defaults to a client built from
            ``settings``.
    """

    def __init__(self, client: PlaneClient | None = None) -> None:
        self._client = client

    def _client_or_none(self) -> PlaneClient | None:
        if self._client is not None:
            return self._client
        if not settings.plane_base_url:
            return None
        return PlaneClient()

    async def ensure_plane_issue(
        self,
        controller_task_id: str,
        title: str,
        description: str | None = None,
        state: TaskState = TaskState.PROPOSED,
        project_id: str | None = None,
        source: str = "api",
        approval_required: bool = True,
        opentasks_id: str | None = None,
    ) -> dict[str, object] | None:
        """Create or update a Plane issue for the given Controller task.

        Returns the Plane issue JSON, or None when Plane integration is not
        configured.
        """
        client = self._client_or_none()
        if client is None:
            return None

        existing = await client.find_issue_by_controller_task_id(
            controller_task_id, project_id=project_id
        )
        if existing is not None:
            return existing

        state_id = await self._resolve_state_id(state, project_id=project_id)
        extra: dict[str, object] = {
            "controller_task_id": controller_task_id,
            "source": source,
            "approval_required": approval_required,
        }
        if opentasks_id is not None:
            extra["opentasks_id"] = opentasks_id

        return await client.create_issue(
            name=title,
            description=description,
            state=state_id,
            project_id=project_id,
            extra=extra,
        )

    async def update_state(
        self,
        controller_task_id: str,
        plane_issue_id: str,
        state: TaskState,
        project_id: str | None = None,
        opentasks_id: str | None = None,
    ) -> dict[str, object] | None:
        """Update the Plane issue state to mirror the Controller state.

        Returns the updated Plane issue, or None when Plane is not configured.
        """
        client = self._client_or_none()
        if client is None:
            return None

        state_id = await self._resolve_state_id(state, project_id=project_id)
        if state_id is None:
            return None

        fields: dict[str, object] = {"state": state_id}
        if opentasks_id is not None:
            fields["opentasks_id"] = opentasks_id

        return await client.update_issue(
            plane_issue_id, fields, project_id=project_id
        )

    async def add_comment(
        self,
        plane_issue_id: str,
        text: str,
        project_id: str | None = None,
    ) -> dict[str, object] | None:
        """Add a comment to a Plane issue.

        Returns the Plane comment JSON, or None when Plane is not configured.
        """
        client = self._client_or_none()
        if client is None:
            return None

        return await client.add_comment(plane_issue_id, text, project_id=project_id)

    async def _resolve_state_id(
        self, state: TaskState, project_id: str | None = None
    ) -> str | None:
        client = self._client_or_none()
        if client is None:
            return None

        plane_name = _CONTROLLER_TO_PLANE_STATE_NAME.get(state)
        if plane_name is None:
            return None

        states_response = await client.list_states(project_id=project_id)
        results = states_response.get("results", states_response)
        if not isinstance(results, list):
            return None

        for state_item in results:
            if not isinstance(state_item, dict):
                continue
            if state_item.get("name") == plane_name:
                state_id = state_item.get("id")
                if isinstance(state_id, str):
                    return state_id

        return None
