"""Audit log REST API endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas.audit_log import AuditLogEntry

router = APIRouter(tags=["audit"])


@router.get("/tasks/{task_id}/audit-log")
async def get_task_audit_log(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogEntry]:
    """Return the append-only audit log for a given task."""
    task = await db.scalar(select(Task).where(Task.id == task_id))  # type: ignore[arg-type]
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.task_id == task_id)  # type: ignore[arg-type]
        .order_by(AuditLog.timestamp, AuditLog.id)  # type: ignore[arg-type]
    )
    entries = result.scalars().all()

    return [
        AuditLogEntry(
            event_id=e.event_id,
            event_type=e.event_type,
            task_id=e.task_id,
            execution_id=e.execution_id,
            actor=e.actor,
            source=e.source,
            timestamp=e.timestamp,
            payload=e.payload,
        )
        for e in entries
    ]
