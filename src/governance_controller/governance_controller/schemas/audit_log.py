"""Audit log API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditLogEntry(BaseModel):
    """A single audit log entry returned by the API."""

    event_id: str
    event_type: str
    task_id: str
    execution_id: str | None
    actor: str
    source: str
    timestamp: datetime
    payload: dict[str, Any]


class AuditLogPage(BaseModel):
    """Paginated audit log response."""

    entries: list[AuditLogEntry]
    total: int
    limit: int = Field(..., ge=1, le=1000)
    offset: int = Field(..., ge=0)
