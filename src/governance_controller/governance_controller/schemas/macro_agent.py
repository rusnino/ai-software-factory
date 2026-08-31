"""Schemas for responses received from the macro-agent service."""

from pydantic import BaseModel, Field


class MacroAgentStartResponse(BaseModel):
    """Validated response returned after starting a macro-agent run."""

    run_id: str = Field(min_length=1)
    status: str | None = None
