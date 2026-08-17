"""Append-only audit log SQLModel entity."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class AuditLog(SQLModel, table=True):
    """An append-only audit log entry."""

    __table_args__ = (
        Index("ix_audit_log_event_id", "event_id"),
        Index("ix_audit_log_task_id", "task_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(index=True)
    event_type: str
    task_id: str = Field(index=True)
    execution_id: str | None = None
    actor: str
    source: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON()),
    )
