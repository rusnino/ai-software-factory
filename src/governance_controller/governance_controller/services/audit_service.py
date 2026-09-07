"""Append-only audit log service."""

from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.audit_log import AuditLog
from governance_controller.services.plane_projection import (
    acquire_plane_projection_event_lock,
)

logger = structlog.get_logger("governance_controller.audit")
_PLANE_PROJECTION_TERMINAL_EVENTS = (
    "plane_projection_completed",
    "plane_projection_failed",
    "plane_projection_skipped",
)


def _is_synthetic_plane_projection_terminal(
    event_type: str,
    source: str,
    payload: dict[str, Any],
) -> bool:
    """Identify a completion synthesized by the orphan-marker sweeper."""
    return (
        event_type == "plane_projection_completed"
        and source == "stuck_execution_poller"
        and (
            payload.get("synthetic") is True
            or payload.get("reason") == "resolved_independently"
        )
    )


class AuditService:
    """Service for creating append-only audit log entries."""

    @staticmethod
    async def log(
        db: AsyncSession,
        event_type: str,
        task_id: str,
        actor: str,
        source: str,
        execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Create a new append-only audit log entry.

        Args:
            db: The SQLAlchemy async session to use.
            event_type: The category of event being recorded.
            task_id: The governed task identifier.
            actor: The actor that triggered the event.
            source: The source system that reported the event.
            execution_id: Optional execution identifier.
            payload: Optional structured event payload.

        Returns:
            The created AuditLog entry.
        """
        event_payload = payload or {}
        pending_event_id = event_payload.get("pending_event_id")
        if (
            event_type in _PLANE_PROJECTION_TERMINAL_EVENTS
            and isinstance(pending_event_id, str)
            and pending_event_id
        ):
            await acquire_plane_projection_event_lock(db, pending_event_id)
            existing_result = await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.task_id == task_id,  # type: ignore[arg-type]
                    AuditLog.__table__.c.event_type.in_(  # type: ignore[attr-defined]
                        _PLANE_PROJECTION_TERMINAL_EVENTS
                    ),
                    AuditLog.payload["pending_event_id"].as_string()
                    == pending_event_id,
                )
                .order_by(AuditLog.__table__.c.id)  # type: ignore[attr-defined]
                .execution_options(populate_existing=True)
            )
            incoming_is_synthetic = _is_synthetic_plane_projection_terminal(
                event_type, source, event_payload
            )
            for existing in existing_result.scalars():
                if incoming_is_synthetic or not _is_synthetic_plane_projection_terminal(
                    existing.event_type, existing.source, existing.payload
                ):
                    return existing

        entry = AuditLog(
            event_id=str(uuid4()),
            event_type=event_type,
            task_id=task_id,
            actor=actor,
            source=source,
            execution_id=execution_id,
            payload=event_payload,
        )
        db.add(entry)
        await db.flush()
        await db.refresh(entry)

        logger.info(
            "audit_log_entry_created",
            event_id=entry.event_id,
            event_type=entry.event_type,
            task_id=entry.task_id,
            actor=entry.actor,
            source=entry.source,
            execution_id=entry.execution_id,
        )

        return entry
