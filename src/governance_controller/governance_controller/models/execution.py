"""Execution SQLModel entity."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Index, String
from sqlmodel import Field, SQLModel

from governance_controller.constants import TaskState


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class Execution(SQLModel, table=True):
    """A recorded macro-agent execution for a governed task."""

    __table_args__ = (
        Index("ix_execution_cancellation_pending", "cancellation_pending", "id"),
    )

    id: str = Field(primary_key=True)
    task_id: str = Field(index=True)
    macro_agent_run_id: str | None = None
    state: TaskState
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    status_error: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )
    cancellation_pending: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            default=False,
            server_default="false",
        ),
    )
    cancellation_claim_token: str | None = Field(
        default=None,
        sa_column=Column(String, nullable=True),
    )
    cancellation_claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
