"""Controller → Plane CE projection service.

Keeps Plane issue state, comments, and dependencies in sync with the
authoritative Controller state. Plane is a projection; the Controller decides
when and how to update it.
"""

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.constants import TaskState

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
    ) -> dict[str, object] | None:
        """Create or update a Plane issue for the given Controller task.

        Returns the Plane issue JSON, or None when Plane integration is not
        configured.
        """
        client = self._client_or_none()
        if client is None:
            return None

        state_id = await self._resolve_state_id(state, project_id=project_id)
        payload: dict[str, object] = {
            "name": title,
            "description_html": description or "",
        }
        if state_id is not None:
            payload["state"] = state_id

        return await client.create_issue(
            name=title,
            description=description,
            state=state_id,
            project_id=project_id,
        )

    async def update_state(
        self,
        controller_task_id: str,
        plane_issue_id: str,
        state: TaskState,
        project_id: str | None = None,
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

        return await client.update_issue_state(
            plane_issue_id, state_id, project_id=project_id
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
