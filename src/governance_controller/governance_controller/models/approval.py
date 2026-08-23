"""Approval SQLModel entity."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel

from governance_controller.constants import ApprovalType


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class Approval(SQLModel, table=True):
    """A recorded human approval for a governed task."""

    __table_args__ = (
        Index("ix_approval_task_id", "task_id"),
        Index(
            "ix_approval_idempotency_key",
            "task_id",
            "idempotency_key",
            "approval_type",
            "actor",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    task_id: str
    approval_type: ApprovalType
    source: str
    actor: str
    timestamp: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
    comment: str | None = None
    idempotency_key: str
