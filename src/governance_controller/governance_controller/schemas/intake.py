"""Schemas for multi-channel intake."""

from pydantic import BaseModel, Field


class RawIdea(BaseModel):
    """A normalized intake item before classification."""

    source: str = Field(..., min_length=1, max_length=128)
    source_id: str = Field(..., min_length=1, max_length=256)
    sender: str = Field(..., min_length=1, max_length=256)
    subject: str = Field(..., min_length=1, max_length=256)
    body: str = Field(..., min_length=1, max_length=16384)


class ClassifiedIdea(BaseModel):
    """Result of classifying a raw intake idea."""

    idea: RawIdea
    # spam, existing_project, new_project, unclear
    category: str = Field(..., min_length=1, max_length=64)
    project_id: str | None = Field(default=None, max_length=128)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=1024)
