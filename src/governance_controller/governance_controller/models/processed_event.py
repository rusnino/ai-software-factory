"""Processed event SQLModel entity for event bridge idempotency."""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class ProcessedEvent(SQLModel, table=True):
    """A durable record of an event that EventBridge has already processed."""

    __table_args__ = (
        Index(
            "ix_processed_event_unique_key",
            "task_id",
            "event_type",
            "event_timestamp",
            "event_id",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    task_id: str
    event_type: str
    event_timestamp: datetime
    event_id: str
    processed_at: datetime = Field(default_factory=utc_now)
