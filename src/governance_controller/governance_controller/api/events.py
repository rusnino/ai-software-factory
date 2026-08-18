"""HTTP endpoint for macro-agent workspace events."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.event_bridge import EventBridge
from governance_controller.db import get_db

router = APIRouter(prefix="/events", tags=["events"])

MAX_BODY_SIZE_BYTES = 64 * 1024


class EventBodySizeLimitMiddleware:
    """ASGI middleware that caps the actual request body read for POST /events.

    FastAPI/Starlette's built-in body parsing reads the entire ASGI stream into
    memory before the route handler runs, so checking only the Content-Length
    header trusts the client. This middleware buffers the whole body itself,
    rejects the request if the buffered bytes exceed the cap, and only then
    hands the buffered messages to the application. A client that omits
    Content-Length, sends a chunked body, or lies about the length is capped by
    the actual bytes received.

    # ponytail: buffers the entire body in memory up to the cap; streaming
    # handling would need a different approach if we ever want to support
    # multi-megabyte event bodies without buffering.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method != "POST" or path.rstrip("/") != "/events":
            await self.app(scope, receive, send)
            return

        buffered: list[dict[str, Any]] = []
        total_bytes = 0
        complete = False

        while not complete:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.request":
                total_bytes += len(message.get("body", b""))
                complete = not message.get("more_body", False)
                if total_bytes > MAX_BODY_SIZE_BYTES:
                    # Drain the rest of the stream before responding so the
                    # transport sees a complete request lifecycle.
                    while not complete:
                        tail = await receive()
                        complete = (
                            tail.get("type") == "http.request"
                            and not tail.get("more_body", False)
                        )
                    await send(
                        {
                            "type": "http.response.start",
                            "status": status.HTTP_413_CONTENT_TOO_LARGE,
                            "headers": [(b"content-type", b"text/plain")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Payload Too Large",
                        }
                    )
                    return
            elif message.get("type") == "http.disconnect":
                return

        index = 0

        async def _replay_receive() -> dict[str, Any]:
            nonlocal index
            if index < len(buffered):
                msg = buffered[index]
                index += 1
                return msg
            return await receive()

        await self.app(scope, _replay_receive, send)


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
