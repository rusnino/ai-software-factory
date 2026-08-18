"""Task SQLModel entity."""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, SQLModel

from governance_controller.constants import TaskState


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class Task(SQLModel, table=True):
    """A unit of work governed by the Governance Controller state machine."""

    id: str = Field(primary_key=True)
    state: TaskState = Field(default=TaskState.PROPOSED)
    version: int = Field(
        default=0,
        sa_column=Column(
            "version", Integer, default=0, nullable=False, server_default="0"
        ),
    )
    project_id: str
    proposed_by: str
    task_contract_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("task_contract_json", JSON()),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
