"""Plane CE webhook receiver.

Translates Plane state-change webhooks into Controller approvals per SPEC-04
§4.3a. Plane CE remains a projection; the Controller is authoritative.

This receiver accepts Plane's real outbound webhook format:

    {
      "event": "issue",
      "action": "update",
      "webhook_id": "...",
      "workspace_id": "...",
      "workspace_slug": "...",
      "data": {"id": "...", "name": "...", "state": "...", "project_id": "..."},
      "activity": {
        "field": "state",
        "old_value": "<state-uuid>",
        "new_value": "<state-uuid>",
        "actor": {"email": "user@example.com", ...},
        ...
      }
    }

Authentication uses Plane's HMAC-SHA256 signature in ``X-Plane-Signature``;
a static ``X-Plane-Webhook-Secret`` header is accepted as a test/dev fallback.
"""

import contextlib
import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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


class _PlaneActor(BaseModel):
    """Actor object inside a Plane webhook activity block."""

    email: str | None = None
    display_name: str | None = None


class _PlaneActivity(BaseModel):
    """Activity block inside a real Plane webhook."""

    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    actor: str | _PlaneActor | None = None


class _PlaneData(BaseModel):
    """Data block inside a real Plane webhook."""

    id: str | None = None
    project_id: str | None = None
    name: str | None = None
    state: str | None = None


class PlaneWebhookEvent(BaseModel):
    """Validated Plane webhook body (real Plane CE shape)."""

    event: str | None = None
    action: str | None = None
    webhook_id: str | None = None
    workspace_id: str | None = None
    workspace_slug: str | None = None
    data: _PlaneData = Field(default_factory=_PlaneData)
    activity: _PlaneActivity = Field(default_factory=_PlaneActivity)

    # Legacy SPEC-04 shape kept for backward compatibility with internal
    # integrations and tests until they migrate to the real Plane format.
    source: str | None = None
    event_type: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


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


def _controller_state(plane_state_name: str | None) -> TaskState | None:
    if not plane_state_name:
        return None
    return _PLANE_STATE_TO_CONTROLLER.get(plane_state_name)


async def _plane_state_name_map(project_id: str | None) -> dict[str, str]:
    """Return a map of Plane state UUID -> Plane state display name.

    Returns an empty map if Plane is not configured or the lookup fails.
    """
    if not project_id:
        return {}
    try:
        response = await PlaneClient().list_states(project_id=project_id)
    except PlaneClientError:
        return {}
    results = response.get("results", response)
    if not isinstance(results, list):
        return {}
    mapping: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        state_id = item.get("id")
        state_name = item.get("name")
        if isinstance(state_id, str) and isinstance(state_name, str):
            mapping[state_id] = state_name
    return mapping


def _actor_from_activity(activity: _PlaneActivity) -> str | None:
    """Extract a string actor identifier from a Plane activity block."""
    raw = activity.actor
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("email") or raw.get("display_name")
    if isinstance(raw, _PlaneActor):
        return raw.email or raw.display_name
    return None


async def _may_translate(
    event: PlaneWebhookEvent,
) -> tuple[ApprovalType, TaskState, TaskState, str, str, str] | None:
    """Return approval type, transition, and ids if the webhook is eligible."""
    # Real Plane CE shape.
    if event.event == "issue" and event.action == "update":
        activity = event.activity
        if activity.field != "state":
            return None
        project_id = event.data.project_id
        issue_id = event.data.id
        if not project_id or not issue_id:
            return None

        state_map = await _plane_state_name_map(project_id)
        previous = _controller_state(state_map.get(activity.old_value or ""))
        current = _controller_state(state_map.get(activity.new_value or ""))
        if previous is None or current is None:
            return None

        approval_type = _TRANSITION_TO_APPROVAL.get((previous, current))
        if approval_type is None:
            return None

        return approval_type, previous, current, issue_id, project_id, ""

    # Legacy SPEC-04 shape.
    if event.source == "plane" and event.event_type == "state.changed":
        payload = event.payload
        actor_type = payload.get("actor_type", "human")
        operation = payload.get("operation", "single_update")
        if actor_type != "human":
            return None
        if operation in {"bulk_update", "migration", "automation", "import"}:
            return None

        previous = _controller_state(payload.get("previous", {}).get("state"))
        current = _controller_state(payload.get("current", {}).get("state"))
        if previous is None or current is None:
            return None

        approval_type = _TRANSITION_TO_APPROVAL.get((previous, current))
        if approval_type is None:
            return None

        task_id = event.task_id or ""
        project_id = event.project_id or ""
        return approval_type, previous, current, task_id, project_id, payload.get(
            "actor", ""
        )

    return None



class WebhookAuthError(Exception):
    """Raised when a webhook fails authentication."""


def _allowed_actor_emails() -> set[str]:
    """Return configured allowed Plane actor emails as a set."""
    raw = settings.plane_webhook_allowed_actors
    if not raw:
        return set()
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def _verify_plane_signature(request: Request, signature_header: str | None) -> bool:
    """Verify Plane's HMAC-SHA256 signature over the raw request body."""
    if not signature_header:
        return False
    configured = settings.plane_webhook_secret
    if not configured:
        return False
    body = getattr(request.state, "raw_body", b"")
    expected = hmac.new(
        configured.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature_header, expected)


def _require_plane_secret(
    request: Request,
    x_plane_signature: str | None = Header(default=None, alias="X-Plane-Signature"),
    x_plane_webhook_secret: str | None = Header(
        default=None, alias="X-Plane-Webhook-Secret"
    ),
) -> None:
    """Validate the Plane webhook.

    Plane CE signs webhooks with HMAC-SHA256 in ``X-Plane-Signature``. The
    endpoint is closed by default. A static ``X-Plane-Webhook-Secret`` header
    is accepted as a local dev/test fallback when the signature header is not
    present.
    """
    configured = settings.plane_webhook_secret
    if not configured:
        raise WebhookAuthError("Plane webhook secret is not configured")

    if x_plane_signature is not None:
        if _verify_plane_signature(request, x_plane_signature):
            return
        raise WebhookAuthError("Invalid Plane webhook signature")

    if not hmac.compare_digest(x_plane_webhook_secret or "", configured):
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

    Requires ``X-Plane-Signature`` (HMAC-SHA256 of the raw body) or a static
    ``X-Plane-Webhook-Secret`` header when ``GC_PLANE_WEBHOOK_SECRET`` is
    configured. If the request is rejected, the Controller reverts the Plane
    state and comments with the reason, because the Controller owns execution
    status.
    """
    translation = await _may_translate(event)

    if translation is None:
        # Not an approval-eligible event. Ignore silently; Plane state stays.
        return

    (
        approval_type,
        previous_plane,
        _current_plane,
        issue_id,
        project_id,
        legacy_actor,
    ) = translation

    # Bind the self-reported actor to a real Plane member email. If we cannot
    # resolve it, reject the webhook so the Controller never acts on a
    # synthetic or spoofed identity.
    allowed_emails = _allowed_actor_emails()
    actor_email: str | None = legacy_actor or _actor_from_activity(event.activity)
    if actor_email is None:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            "No actor found in webhook activity",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No actor found in webhook activity",
        )

    if not allowed_emails:
        # No allow-list means no actor is trusted. Reject the webhook so an
        # operator cannot accidentally enable the webhook with unverified actors.
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            "No allowed actors configured for webhook approvals",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No allowed actors configured",
        )

    if allowed_emails:
        resolved: str | None = None
        if settings.plane_base_url:
            resolved = await _resolve_actor_email(PlaneClient(), actor_email)
        if resolved is None:
            # Without Plane connectivity we cannot verify the actor is a real
            # workspace member, so any configured allow-list blocks the request.
            await _revert_plane_state(
                issue_id,
                event.activity.old_value,
                f"Actor '{actor_email}' could not be verified",
                project_id=project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unresolvable actor",
            )
        actor_email = resolved
        if actor_email not in allowed_emails:
            await _revert_plane_state(
                issue_id,
                event.activity.old_value,
                f"Actor '{actor_email}' is not authorised to approve via webhook",
                project_id=project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Actor not authorised",
            )

    task_service = TaskService(db)
    task = await task_service.get_by_plane_issue_id(issue_id)
    if task is None:
        # Fall back to matching by Controller task id for legacy/internal tasks.
        task = await task_service.get_by_id(issue_id)
    if task is None:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            f"Task {issue_id} not found in Controller",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {issue_id} not found",
        )

    # Reject stale webhooks: current Controller state must match the webhook's
    # previous Plane state.
    if task.state != previous_plane:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            (
                "Stale webhook: Controller state is "
                f"{task.state.value}, expected {previous_plane.value}"
            ),
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stale webhook: task state does not match",
        )

    profile = await task_service.get_profile_by_project_id(task.project_id)
    if profile is None:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            "Project profile not found for task",
            project_id=project_id,
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
            actor=actor_email or "",
            idempotency_key=(
                f"plane-{issue_id}-{approval_type.value}-"
                f"{event.activity.new_value or ''}"
            ),
            comment=None,
        )
    except PolicyViolationError as exc:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            f"Policy violation: {exc.violations}",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": str(exc), "violations": exc.violations},
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if "Invalid transition" in message or "Concurrent modification" in message:
            await _revert_plane_state(
                issue_id,
                event.activity.old_value,
                f"State conflict: {message}",
                project_id=project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            f"Approval rejected: {message}",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
        ) from exc
    except RuntimeError as exc:
        await _revert_plane_state(
            issue_id,
            event.activity.old_value,
            f"Execution unavailable: {exc}",
            project_id=project_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _revert_plane_state(
    task_id: str,
    previous_state: Any,
    reason: str,
    project_id: str | None = None,
) -> None:
    """Revert Plane state and comment when Controller rejects the change.

    This is a best-effort projection correction. Failures are logged but not
    raised, because the webhook must still return its HTTP error to Plane.
    When ``previous_state`` is a Plane state UUID, the issue state is moved
    back to it; otherwise only a comment is added (#156).
    """
    if not settings.plane_base_url or not settings.plane_api_token:
        return

    client = PlaneClient()
    if isinstance(previous_state, str) and previous_state:
        with contextlib.suppress(Exception):
            await client.update_issue_state(
                task_id,
                state_id=previous_state,
                project_id=project_id,
            )
    with contextlib.suppress(Exception):
        await client.add_comment(
            task_id,
            f"Controller rejected state change: {reason}",
            project_id=project_id,
        )
