"""HTTP endpoint for macro-agent workspace events."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from governance_controller.adapters.macro_agent.event_bridge import EventBridge
from governance_controller.db import get_db

router = APIRouter(prefix="/events", tags=["events"])

MAX_BODY_SIZE_BYTES = 64 * 1024


class EventBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject POST /events requests whose Content-Length exceeds the limit."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "POST" and request.url.path.rstrip("/") == "/events":
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > MAX_BODY_SIZE_BYTES:
                return Response(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content=b"Payload Too Large",
                )
        return await call_next(request)


class EventIn(BaseModel):
    """Validated request body for a macro-agent workspace event."""

    type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def receive_event(
    event: EventIn,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Receive a macro-agent workspace event and translate it into controller state.

    This is the live HTTP entry point for the Event Bridge described in
    SPEC-05 §5.4/§5.7. It is intentionally minimal for Phase 1: events are
    processed synchronously and state transitions use compare-and-swap guards.

    Raises:
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
