"""Approval API schemas."""

from pydantic import BaseModel, Field

from governance_controller.constants import ApprovalType


class ApprovalRequest(BaseModel):
    """Payload for a single authoritative approval."""

    task_id: str = Field(..., min_length=1, max_length=128)
    approval_type: ApprovalType
    source: str = Field(..., min_length=1, max_length=128)
    actor: str = Field(..., min_length=1, max_length=256)
    timestamp: str = Field(..., min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=4096)


class ApprovalResponse(BaseModel):
    """Response from the authoritative approval endpoint."""

    task_id: str
    state: str
    approved: bool
