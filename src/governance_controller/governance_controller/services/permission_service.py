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


def _configured_known_proposers() -> set[str]:
    """Return the configured proposer allow-list from ``GC_KNOWN_PROPOSERS``.

    Unlike ``admins``, empty configuration here means "no restriction" rather
    than "deny": ``TaskContract.proposed_by`` is required for every task
    today, and this Controller has no authenticated per-caller identity to
    validate it against, so defaulting to fail-closed would reject all task
    creation for every existing deployment. This allow-list is an opt-in
    mitigation (#376) an operator can configure once real proposer identities
    are known; leaving it unset preserves current behavior.
    """
    raw = settings.known_proposers
    if not raw:
        return set()
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


class PermissionService:
    """Simple allow-list permission validator for approval requests.

    Phase 1 implementation: no RBAC/OPA integration. Admins are read from the
    ``GC_ADMINS`` environment variable; empty configuration means EXECUTION and
    MERGE approvals are denied by default. System or agent actors are never
    permitted to approve.
    """

    def __init__(
        self,
        admins: set[str] | None = None,
        known_proposers: set[str] | None = None,
    ) -> None:
        self.admins: set[str] = admins if admins is not None else _configured_admins()
        self.known_proposers: set[str] = (
            known_proposers
            if known_proposers is not None
            else _configured_known_proposers()
        )

    @staticmethod
    def _normalize_actor(actor: str) -> str:
        """Return a normalized actor identifier.

        Strips surrounding whitespace, folds to lower case, and removes ASCII
        control / zero-width characters so casing/whitespace cannot bypass
        system/agent blocks or self-approval checks.
        """
        stripped = actor.strip()
        # Strip zero-width and control characters commonly used to evade
        # simple string checks.
        cleaned = "".join(
            ch
            for ch in stripped
            if ch.isprintable() or ch.isspace()
        )
        return cleaned.strip().lower()

    async def may_approve(
        self,
        actor: str,
        task_id: str,
        approval_type: ApprovalType,
    ) -> bool:
        """Return whether ``actor`` may request ``approval_type`` for ``task_id``."""
        normalized = self._normalize_actor(actor)

        if (
            normalized == "system"
            or normalized == "agent"
            or normalized.startswith("system:")
            or normalized.startswith("agent:")
        ):
            return False

        if approval_type == ApprovalType.PLAN:
            return True

        return normalized in {a.strip().lower() for a in self.admins}

    def is_recognized_proposer(self, proposed_by: str) -> bool:
        """Return whether ``proposed_by`` is an acceptable task-proposer identity.

        When ``known_proposers`` is unconfigured (the default), every value is
        accepted -- ``proposed_by`` remains as unauthenticated as it is today.
        Once configured, ``proposed_by`` must normalize to one of the
        allow-listed identities, giving the self-approval guard (#376) a
        closed universe to compare against instead of an arbitrary,
        client-supplied string chosen to differ from whatever actor later
        approves the task.
        """
        if not self.known_proposers:
            return True
        normalized = self._normalize_actor(proposed_by)
        return normalized in {p.strip().lower() for p in self.known_proposers}
