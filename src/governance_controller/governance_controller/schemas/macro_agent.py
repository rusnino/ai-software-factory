"""Schemas for responses received from the macro-agent service."""

from pydantic import BaseModel, Field, field_validator


class MacroAgentStartResponse(BaseModel):
    """Validated response returned after starting a macro-agent run."""

    run_id: str = Field(min_length=1)
    status: str | None = None

    @field_validator("run_id")
    @classmethod
    def _non_blank_run_id(cls, value: str) -> str:
        """Reject an ID that cannot address a macro-agent run."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id must not be blank")
        return normalized
