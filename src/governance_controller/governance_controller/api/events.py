"""HTTP endpoint for macro-agent workspace events."""

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.event_bridge import EventBridge
from governance_controller.config import settings
from governance_controller.db import get_db

router = APIRouter(prefix="/events", tags=["events"])


class EventAuthError(Exception):
    """Raised when an event request fails authentication."""


class EventIn(BaseModel):
    """Validated request body for a macro-agent workspace event."""

    type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


def _require_event_bridge_secret(
    x_event_bridge_secret: str | None = Header(
        default=None, alias="X-Event-Bridge-Secret"
    ),
) -> None:
    """Validate the macro-agent Event Bridge shared secret.

    The endpoint is closed by default. If ``event_bridge_secret`` is not
    configured, or if the request presents the wrong secret, the request is
    rejected.
    """
    configured = settings.event_bridge_secret
    if not configured or not hmac.compare_digest(
        x_event_bridge_secret or "", configured
    ):
        raise EventAuthError("Invalid or missing Event Bridge secret")


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def receive_event(
    event: EventIn,
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_event_bridge_secret),
) -> None:
    """Receive a macro-agent workspace event and translate it into controller state.

    This is the live HTTP entry point for the Event Bridge described in
    SPEC-05 §5.4/§5.7. It requires ``X-Event-Bridge-Secret`` when
    ``GC_EVENT_BRIDGE_SECRET`` is configured.

    Raises:
        HTTPException: 401 Unauthorized if the shared secret is missing/invalid.
        HTTPException: 409 Conflict if the event's transitions loses a CAS race.
    """
    try:
        await EventBridge.handle(db, event.model_dump())
    except ValueError as exc:
        # Do not record a processed-event key for rejected CAS races so the
        # caller can retry.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
