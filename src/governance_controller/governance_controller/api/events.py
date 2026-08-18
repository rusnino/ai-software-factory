"""HTTP endpoint for macro-agent workspace events."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.macro_agent.event_bridge import EventBridge
from governance_controller.db import get_db

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def receive_event(
    event: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> None:
    """Receive a macro-agent workspace event and translate it into controller state.

    This is the live HTTP entry point for the Event Bridge described in
    SPEC-05 §5.4/§5.7. It is intentionally minimal for Phase 1: events are
    processed synchronously and state transitions use compare-and-swap guards.
    """
    try:
        await EventBridge.handle(db, event)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
