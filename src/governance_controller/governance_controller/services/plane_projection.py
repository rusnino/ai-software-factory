"""Controller → Plane CE projection service.

Keeps Plane issue state, comments, and dependencies in sync with the
authoritative Controller state. Plane is a projection; the Controller decides
when and how to update it.
"""

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient, PlaneClientError
from governance_controller.config import settings
from governance_controller.constants import TaskState


def _plane_projection_lock_key(controller_task_id: str) -> int:
    """Return a stable advisory-lock key for one Controller task."""
    digest = hashlib.sha256(
        b"plane-projection:" + controller_task_id.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _plane_projection_event_lock_key(pending_event_id: str) -> int:
    """Return a stable advisory-lock key for one pending projection event."""
    digest = hashlib.sha256(
        b"plane-projection-event:" + pending_event_id.encode("utf-8")
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


async def acquire_plane_projection_event_lock(
    db: AsyncSession, pending_event_id: str
) -> None:
    """Serialize terminal audit outcomes for one pending projection event.

    The caller must commit or roll back after checking or recording the
    terminal outcome. SQLite uses an immediate write transaction because it has
    no advisory-lock equivalent; the database-wide writer lock is intentional
    for its test/development deployment.
    """
    bind = db.bind
    if bind is None:
        return
    if bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _plane_projection_event_lock_key(pending_event_id)},
        )
    elif bind.dialect.name == "sqlite":
        # SQLite cannot upgrade an existing read transaction to IMMEDIATE.
        # Terminal projection callers have already completed their external
        # work, so ending that transaction is safer than allowing an
        # unprotected check-then-insert race.
        if db.in_transaction():
            await db.commit()
        await db.execute(text("BEGIN IMMEDIATE"))

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

    @staticmethod
    def _source_property_value(source: str | None) -> str | None:
        if not settings.plane_source_property_id or source is None:
            return source

        option_id = settings.plane_source_option_ids.get(source)
        if not isinstance(option_id, str) or not option_id.strip():
            raise PlaneClientError(
                "Plane source property is configured but no non-empty option "
                f"UUID is configured for source {source!r}"
            )
        return option_id.strip()

    async def _write_property_value(
        self,
        client: PlaneClient,
        issue_id: str,
        field_name: str,
        property_id: str,
        value: str | bool | None,
        project_id: str | None,
    ) -> None:
        if not property_id or value is None:
            return
        try:
            await client.upsert_work_item_property_value(
                issue_id,
                property_id,
                value,
                project_id=project_id,
            )
        except PlaneClientError as exc:
            raise PlaneClientError(
                f"Plane property projection failed for {field_name} "
                f"on issue {issue_id}: {exc}"
            ) from exc

    async def _write_configured_properties(
        self,
        client: PlaneClient,
        issue: dict[str, object],
        controller_task_id: str,
        source: str | None,
        approval_required: bool | None,
        opentasks_id: str | None,
        project_id: str | None,
    ) -> None:
        configured = (
            (
                "controller_task_id",
                settings.plane_controller_task_id_property_id,
                controller_task_id,
            ),
            ("opentasks_id", settings.plane_opentasks_id_property_id, opentasks_id),
            ("source", settings.plane_source_property_id, source),
            (
                "approval_required",
                settings.plane_approval_required_property_id,
                approval_required,
            ),
        )
        if not any(
            property_id and value is not None
            for _, property_id, value in configured
        ):
            return

        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            raise PlaneClientError(
                "Plane issue response is missing a usable id for property projection"
            )

        for field_name, property_id, value in configured:
            await self._write_property_value(
                client,
                issue_id,
                field_name,
                property_id,
                value,
                project_id,
            )

    async def ensure_plane_issue(
        self,
        controller_task_id: str,
        title: str,
        description: str | None = None,
        state: TaskState = TaskState.PROPOSED,
        project_id: str | None = None,
        source: str | None = None,
        approval_required: bool | None = None,
        opentasks_id: str | None = None,
    ) -> dict[str, object] | None:
        """Create or update a Plane issue for the given Controller task.

        Returns the Plane issue JSON, or None when Plane integration is not
        configured.
        """
        client = self._client_or_none()
        if client is None:
            return None

        source_property_value = self._source_property_value(source)
        existing = await client.find_issue_by_controller_task_id(
            controller_task_id, project_id=project_id
        )
        if existing is not None:
            await self._write_configured_properties(
                client,
                existing,
                controller_task_id,
                source_property_value,
                approval_required,
                opentasks_id,
                project_id,
            )
            return existing

        state_id = await self._resolve_state_id(state, project_id=project_id)

        issue = await client.create_issue(
            name=title,
            description=description,
            state=state_id,
            project_id=project_id,
            external_id=controller_task_id,
            external_source="governance-controller",
        )
        await self._write_configured_properties(
            client,
            issue,
            controller_task_id,
            source_property_value,
            approval_required,
            opentasks_id,
            project_id,
        )
        return issue

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

        result = await client.update_issue_state(
            plane_issue_id, state_id, project_id=project_id
        )
        await self._write_property_value(
            client,
            plane_issue_id,
            "opentasks_id",
            settings.plane_opentasks_id_property_id,
            opentasks_id,
            project_id,
        )
        return result

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
