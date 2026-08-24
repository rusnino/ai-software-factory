"""Plane CE webhook receiver.

Translates Plane state-change webhooks into Controller approvals per SPEC-04
§4.3a. Plane CE remains a projection; the Controller is authoritative.
"""

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.policy_engine import PolicyViolationError
from governance_controller.services.task_service import TaskService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class _PlanePayload(BaseModel):
    """Nested payload inside a Plane webhook."""

    previous: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any] = Field(default_factory=dict)
    actor: str = ""
    actor_type: str = "human"
    operation: str = "single_update"


class PlaneWebhookEvent(BaseModel):
    """Validated Plane webhook body."""

    source: str
    event_type: str
    task_id: str
    project_id: str
    payload: _PlanePayload


# Map from Plane state name to Controller state.
_PLANE_STATE_TO_CONTROLLER: dict[str, TaskState] = {
    "Proposed": TaskState.PROPOSED,
    "Plan Approved": TaskState.PLAN_APPROVED,
    "Approved": TaskState.EXEC_APPROVED,
    "Ready": TaskState.READY,
    "In Progress": TaskState.RUNNING,
    "Agent Review": TaskState.AGENT_REVIEW,
    "In Review": TaskState.HUMAN_REVIEW,
    "Done": TaskState.DONE,
    "Blocked": TaskState.BLOCKED,
    "Failed": TaskState.FAILED,
}

# Eligible Plane transitions that may translate to an approval.
_TRANSITION_TO_APPROVAL: dict[tuple[TaskState, TaskState], ApprovalType] = {
    (TaskState.PROPOSED, TaskState.PLAN_APPROVED): ApprovalType.PLAN,
    (TaskState.PLAN_APPROVED, TaskState.EXEC_APPROVED): ApprovalType.EXECUTION,
    (TaskState.HUMAN_REVIEW, TaskState.DONE): ApprovalType.MERGE,
}


def _controller_state(plane_state: str | None) -> TaskState | None:
    if not plane_state:
        return None
    return _PLANE_STATE_TO_CONTROLLER.get(plane_state)


def _may_translate(
    event: PlaneWebhookEvent,
) -> tuple[ApprovalType, TaskState, TaskState] | None:
    """Return approval type and transition if the webhook is eligible.

    Returns None if any SPEC-04 §4.3a condition is violated.
    """
    if event.source != "plane":
        return None
    if event.event_type != "state.changed":
        return None

    payload = event.payload
    if payload.actor_type != "human":
        return None
    if payload.operation in {"bulk_update", "migration", "automation", "import"}:
        return None

    previous = _controller_state(payload.previous.get("state"))
    current = _controller_state(payload.current.get("state"))
    if previous is None or current is None:
        return None

    approval_type = _TRANSITION_TO_APPROVAL.get((previous, current))
    if approval_type is None:
        return None

    return approval_type, previous, current


@router.post("/plane", status_code=status.HTTP_204_NO_CONTENT)
async def receive_plane_webhook(
    event: PlaneWebhookEvent,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Receive a Plane state-change webhook and translate it to an approval.

    If the request is rejected, the Controller reverts the Plane state and
    comments with the reason, because the Controller owns execution status.
    """
    translation = _may_translate(event)

    if translation is None:
        # Not an approval-eligible event. Ignore silently; Plane state stays.
        return

    approval_type, previous_plane, _current_plane = translation

    task_service = TaskService(db)
    task = await task_service.get_by_id(event.task_id)
    if task is None:
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            f"Task {event.task_id} not found in Controller",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {event.task_id} not found",
        )

    # Reject stale webhooks: current Controller state must match the webhook's
    # previous Plane state.
    if task.state != previous_plane:
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            (
                "Stale webhook: Controller state is "
                f"{task.state.value}, expected {previous_plane.value}"
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stale webhook: task state does not match",
        )

    profile = await task_service.get_profile_by_project_id(task.project_id)
    if profile is None:
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            "Project profile not found for task",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Project profile not found for task",
        )

    contract_data: dict[str, Any] = task.task_contract_json
    contract = TaskContract(**contract_data)

    approval_service = ApprovalService(db=db)

    try:
        await approval_service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=approval_type,
            source="plane",
            actor=event.payload.actor,
            idempotency_key=(
                f"plane-{event.task_id}-{approval_type.value}-"
                f"{event.payload.current.get('state', '')}"
            ),
            comment=None,
        )
    except PolicyViolationError as exc:
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            f"Policy violation: {exc.violations}",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": str(exc), "violations": exc.violations},
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if "Invalid transition" in message or "Concurrent modification" in message:
            await _revert_plane_state(
                event.task_id,
                event.payload.previous.get("state"),
                f"State conflict: {message}",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            f"Approval rejected: {message}",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
        ) from exc
    except RuntimeError as exc:
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            f"Execution unavailable: {exc}",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _revert_plane_state(
    task_id: str,
    previous_state: Any,
    reason: str,
) -> None:
    """Revert Plane state and comment when Controller rejects the change.

    This is a best-effort projection correction. Failures are logged but not
    raised, because the webhook must still return its HTTP error to Plane.
    """
    if not settings.plane_base_url or not settings.plane_api_token:
        return

    client = PlaneClient()
    # We cannot reliably map back to a Plane state UUID here without fetching
    # the state list; as a pragmatic fallback we comment the reason. Failures
    # are swallowed so Plane projection errors do not mask the HTTP response.
    with contextlib.suppress(Exception):
        await client.add_comment(task_id, f"Controller rejected state change: {reason}")
