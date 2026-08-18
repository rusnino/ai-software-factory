"""Audit log API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
