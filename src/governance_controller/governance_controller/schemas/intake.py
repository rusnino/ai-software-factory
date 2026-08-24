"""Schemas for multi-channel intake."""

from pydantic import BaseModel


class RawIdea(BaseModel):
    """A normalized intake item before classification."""

    source: str
    source_id: str
    sender: str
    subject: str
    body: str


class ClassifiedIdea(BaseModel):
    """Result of classifying a raw intake idea."""

    idea: RawIdea
    category: str  # spam, existing_project, new_project, unclear
    project_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
