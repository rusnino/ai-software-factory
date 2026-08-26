"""Minimal permission service stub for the Governance Controller."""

from governance_controller.config import settings
from governance_controller.constants import ApprovalType


def _configured_admins() -> set[str]:
    """Return the configured admin set from ``GC_ADMINS``.

    Empty configuration means no actor has default admin rights, so the
    service fails closed until an admin list is explicitly provided.
    """
    raw = settings.admins
    if not raw:
        return set()
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


class PermissionService:
    """Simple allow-list permission validator for approval requests.

    Phase 1 implementation: no RBAC/OPA integration. Admins are read from the
    ``GC_ADMINS`` environment variable; empty configuration means EXECUTION and
    MERGE approvals are denied by default. System or agent actors are never
    permitted to approve.
    """

    def __init__(self, admins: set[str] | None = None) -> None:
        self.admins: set[str] = admins if admins is not None else _configured_admins()

    async def may_approve(
        self,
        actor: str,
        task_id: str,
        approval_type: ApprovalType,
    ) -> bool:
        """Return whether ``actor`` may request ``approval_type`` for ``task_id``."""
        if (
            actor == "system"
            or actor == "agent"
            or actor.startswith("system:")
            or actor.startswith("agent:")
        ):
            return False

        if approval_type == ApprovalType.PLAN:
            return True

        return actor in self.admins
