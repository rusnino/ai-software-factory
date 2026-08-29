"""Audit log REST API endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.api.auth import require_controller_secret
from governance_controller.db import get_db
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas.audit_log import AuditLogEntry, AuditLogPage

router = APIRouter(tags=["audit"])

_DEFAULT_PAGE_LIMIT = 100
_MAX_PAGE_LIMIT = 1000


@router.get("/tasks/{task_id}/audit-log")
async def get_task_audit_log(
    task_id: str,
    limit: int = Query(
        default=_DEFAULT_PAGE_LIMIT,
        ge=1,
        le=_MAX_PAGE_LIMIT,
        description="Maximum number of entries to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of entries to skip",
    ),
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(require_controller_secret),
) -> AuditLogPage:
    """Return the append-only audit log for a given task.

    Requires ``X-Controller-Secret`` when ``GC_CONTROLLER_API_SECRET`` is
    configured. Results are paginated; the default page size is
    ``_DEFAULT_PAGE_LIMIT`` and the maximum is ``_MAX_PAGE_LIMIT``.
    """
    task = await db.scalar(select(Task).where(Task.id == task_id))  # type: ignore[arg-type]
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    total_result = await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.task_id == task_id)  # type: ignore[arg-type]
    )
    total = total_result.scalar_one()

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.task_id == task_id)  # type: ignore[arg-type]
        .order_by(AuditLog.timestamp, AuditLog.id)  # type: ignore[arg-type]
        .offset(offset)
        .limit(limit)
    )
    entries = result.scalars().all()

    return AuditLogPage(
        entries=[
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
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
