"""Execution SQLModel entity."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from governance_controller.constants import TaskState


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class Execution(SQLModel, table=True):
    """A recorded macro-agent execution for a governed task."""

    id: str = Field(primary_key=True)
    task_id: str = Field(index=True)
    macro_agent_run_id: str | None = None
    state: TaskState
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
