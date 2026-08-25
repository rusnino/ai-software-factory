"""Intake submission tracking for duplicate and rate-limit protection."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class IntakeSubmission(SQLModel, table=True):
    """A durable record of an intake request that produced a Plane draft."""

    __table_args__ = (
        Index(
            "ix_intake_submission_source_source_id",
            "source",
            "source_id",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str
    source_id: str
    sender: str
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
