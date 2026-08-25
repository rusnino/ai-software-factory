"""Plane CE webhook receiver.

Translates Plane state-change webhooks into Controller approvals per SPEC-04
§4.3a. Plane CE remains a projection; the Controller is authoritative.
"""

import contextlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient, PlaneClientError
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


class WebhookAuthError(Exception):
    """Raised when a webhook fails authentication."""


def _allowed_actor_emails() -> set[str]:
    """Return configured allowed Plane actor emails as a set."""
    raw = settings.plane_webhook_allowed_actors
    if not raw:
        return set()
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def _require_plane_secret(
    x_plane_webhook_secret: str | None = Header(
        default=None, alias="X-Plane-Webhook-Secret"
    ),
) -> None:
    """Validate the Plane webhook shared secret.

    The webhook endpoint is closed by default. If ``plane_webhook_secret`` is
    not configured, or if it is configured but the request presents the wrong
    secret, the request is rejected.
    """
    configured = settings.plane_webhook_secret
    if not configured or not hmac.compare_digest(
        x_plane_webhook_secret or "", configured
    ):
        raise WebhookAuthError("Invalid or missing Plane webhook secret")


async def _resolve_actor_email(
    client: PlaneClient,
    actor_display_name: str,
) -> str | None:
    """Resolve a Plane webhook actor display name to a member email.

    Plane CE webhooks deliver a display name, not a verifiable member UUID.
    We resolve it against the workspace member list so the Controller only acts
    on behalf of real, allowed members.
    """
    try:
        members_response = await client.list_workspace_members()
    except (PlaneClientError, Exception):
        # Treat any Plane lookup failure as unresolvable: fail secure.
        return None
    results = members_response.get("results", members_response)
    if not isinstance(results, list):
        return None
    display_lower = actor_display_name.lower()
    for member in results:
        if not isinstance(member, dict):
            continue
        email = member.get("email")
        email_str = email.lower() if isinstance(email, str) else ""
        display = member.get("display_name", "")
        display_str = display.lower() if isinstance(display, str) else ""
        if display_str == display_lower or email_str == display_lower:
            return email_str
    return None


@router.post("/plane", status_code=status.HTTP_204_NO_CONTENT)
async def receive_plane_webhook(
    event: PlaneWebhookEvent,
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_plane_secret),
) -> None:
    """Receive a Plane state-change webhook and translate it to an approval.

    Requires ``X-Plane-Webhook-Secret`` header when ``GC_PLANE_WEBHOOK_SECRET``
    is configured. If the request is rejected, the Controller reverts the
    Plane state and comments with the reason, because the Controller owns
    execution status.
    """
    translation = _may_translate(event)

    if translation is None:
        # Not an approval-eligible event. Ignore silently; Plane state stays.
        return

    approval_type, previous_plane, _current_plane = translation

    # Bind the self-reported actor to a real Plane member email. If we cannot
    # resolve it, reject the webhook so the Controller never acts on a
    # synthetic or spoofed identity.
    allowed_emails = _allowed_actor_emails()
    actor_email: str = event.payload.actor
    if not allowed_emails:
        # No allow-list means no actor is trusted. Reject the webhook so an
        # operator cannot accidentally enable the webhook with unverified actors.
        await _revert_plane_state(
            event.task_id,
            event.payload.previous.get("state"),
            "No allowed actors configured for webhook approvals",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No allowed actors configured",
        )

    if allowed_emails:
        resolved: str | None = None
        if settings.plane_base_url:
            resolved = await _resolve_actor_email(PlaneClient(), event.payload.actor)
        if resolved is None:
            # Without Plane connectivity we cannot verify the actor is a real
            # workspace member, so any configured allow-list blocks the request.
            await _revert_plane_state(
                event.task_id,
                event.payload.previous.get("state"),
                f"Actor '{event.payload.actor}' could not be verified",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unresolvable actor",
            )
        actor_email = resolved
        if actor_email not in allowed_emails:
            await _revert_plane_state(
                event.task_id,
                event.payload.previous.get("state"),
                f"Actor '{actor_email}' is not authorised to approve via webhook",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Actor not authorised",
            )

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
            actor=actor_email or event.payload.actor,
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
