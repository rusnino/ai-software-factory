"""Execution API schema."""

from datetime import datetime

from pydantic import BaseModel


class ExecutionResponse(BaseModel):
    """API response containing execution runtime status."""

    id: str
    task_id: str
    macro_agent_run_id: str | None
    state: str
    started_at: datetime
    ended_at: datetime | None
