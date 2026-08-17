"""Append-only audit log service."""

from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.audit_log import AuditLog

logger = structlog.get_logger("governance_controller.audit")


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
        entry = AuditLog(
            event_id=str(uuid4()),
            event_type=event_type,
            task_id=task_id,
            actor=actor,
            source=source,
            execution_id=execution_id,
            payload=payload or {},
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
