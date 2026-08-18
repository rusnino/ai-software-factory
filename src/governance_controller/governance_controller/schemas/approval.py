"""Approval API schemas."""

from pydantic import BaseModel

from governance_controller.constants import ApprovalType


class ApprovalRequest(BaseModel):
    """Payload for a single authoritative approval."""

    task_id: str
    approval_type: ApprovalType
    source: str
    actor: str
    timestamp: str
    comment: str | None = None


class ApprovalResponse(BaseModel):
    """Response from the authoritative approval endpoint."""

    task_id: str
    state: str
    approved: bool
