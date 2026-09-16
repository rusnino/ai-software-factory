"""Minimal permission service stub for the Governance Controller."""

import structlog

from governance_controller.config import settings
from governance_controller.constants import ApprovalType

logger = structlog.get_logger("governance_controller.permission_service")


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

    Like ``admins``, empty configuration means "deny", not "allow" (#376
    residual: a GitHub-reopened live reproduction showed the previous
    opt-in/empty-means-allow default left every out-of-the-box deployment
    exactly as vulnerable as before the original fix). ``proposed_by`` is
    unauthenticated free text, so an unconfigured allow-list must not let a
    ghost identity through -- operators must explicitly enumerate the real
    proposer identities before any task can be approved.
    """
    raw = settings.known_proposers
    if not raw:
        return set()
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def warn_if_permission_allowlists_empty() -> None:
    """Emit a startup warning when ``GC_ADMINS``/``GC_KNOWN_PROPOSERS`` are empty.

    Both resolve to a fail-closed deny-all when unconfigured (#376), which is
    the correct security posture but gives no explicit signal to an operator
    who deploys with defaults: every task proposal/approval is silently
    rejected with nothing in the startup log explaining why (#388). Call this
    once at application startup so the cause is visible instead of only
    discoverable via a stuck pipeline.
    """
    empty_settings = []
    if not _configured_admins():
        empty_settings.append("GC_ADMINS")
    if not _configured_known_proposers():
        empty_settings.append("GC_KNOWN_PROPOSERS")

    if empty_settings:
        logger.warning(
            "permission_allowlist_empty_fail_closed",
            empty_settings=empty_settings,
        )


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
        human_approval_verified: bool = False,
    ) -> None:
        self.admins: set[str] = admins if admins is not None else _configured_admins()
        self.known_proposers: set[str] = (
            known_proposers
            if known_proposers is not None
            else _configured_known_proposers()
        )
        # Proof -- established by the caller from a channel independent of
        # whatever credential proposed the task -- that a genuinely distinct,
        # human-controlled party is requesting this approval (#376 residual:
        # "propose as agent-1, approve as admin" succeeded even with a fully
        # configured known-proposers allow-list, because both names are just
        # unauthenticated strings the same single caller can freely choose).
        # Defaults to False (deny) deliberately: unlike ``admins``/
        # ``known_proposers`` this is never sourced from a config toggle --
        # a config flag proves nothing about who actually made the request,
        # so it would just reintroduce the "misconfigurable opt-in" pattern
        # this fix removes. Callers must derive it, per request, from a real
        # authentication signal (e.g. a separate ``GC_HUMAN_APPROVAL_SECRET``
        # header, or Plane's own webhook-authenticated actor).
        self.human_approval_verified = human_approval_verified

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

        # EXECUTION/MERGE are the approval types that actually trigger real
        # side effects (macro-agent execution, merge) -- per #376, admin
        # membership by *name* is not enough, since names are unauthenticated
        # and the same caller who proposed the task could simply also claim
        # to be a listed admin. Require independent proof of a distinct human
        # approval channel in addition to admin-list membership.
        if not self.human_approval_verified:
            return False

        return normalized in {a.strip().lower() for a in self.admins}

    def is_recognized_proposer(self, proposed_by: str) -> bool:
        """Return whether ``proposed_by`` is an acceptable task-proposer identity.

        Like ``admins``, an unconfigured (empty) ``known_proposers`` means
        DENY, not "no restriction" (#376 residual: an earlier version of this
        check accepted every value when unconfigured, which a GitHub-reopened
        live reproduction showed left every out-of-the-box deployment exactly
        as vulnerable as before). Once configured, ``proposed_by`` must
        normalize to one of the allow-listed identities, giving the
        self-approval guard a closed universe to compare against instead of
        an arbitrary, client-supplied string chosen to differ from whatever
        actor later approves the task.
        """
        if not self.known_proposers:
            return False
        normalized = self._normalize_actor(proposed_by)
        return normalized in {p.strip().lower() for p in self.known_proposers}
