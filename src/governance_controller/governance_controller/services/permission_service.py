"""Minimal permission service stub for the Governance Controller."""

from governance_controller.constants import ApprovalType


class PermissionService:
    """Simple allow-list permission validator for approval requests.

    Phase 1 implementation: no RBAC/OPA integration. Admins are supplied
    explicitly; default admin is ``"admin"``. System or agent actors are
    never permitted to approve.
    """

    def __init__(self, admins: set[str] | None = None) -> None:
        self.admins: set[str] = admins if admins is not None else {"admin"}

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
