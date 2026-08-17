"""Shared constants for the Governance Controller."""

from enum import StrEnum


class TaskState(StrEnum):
    """Canonical states for a Task lifecycle."""

    PROPOSED = "PROPOSED"
    PLAN_APPROVED = "PLAN_APPROVED"
    EXEC_APPROVED = "EXEC_APPROVED"
    READY = "READY"
    RUNNING = "RUNNING"
    AGENT_REVIEW = "AGENT_REVIEW"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ApprovalType(StrEnum):
    """Types of human approval managed by the Governance Controller."""

    PLAN = "plan"
    EXECUTION = "execution"
    MERGE = "merge"
