"""Tests for the PermissionService stub."""

import pytest
import structlog

from governance_controller.constants import ApprovalType
from governance_controller.services.permission_service import (
    PermissionService,
    warn_if_permission_allowlists_empty,
)


@pytest.fixture
def service() -> PermissionService:
    return PermissionService(admins={"admin"})


async def test_default_permission_service_reads_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#226: default PermissionService reads GC_ADMINS from config."""
    from governance_controller.config import settings

    monkeypatch.setattr(settings, "admins", "alice,bob")

    from governance_controller.services.permission_service import _configured_admins

    assert _configured_admins() == {"alice", "bob"}


class TestPermissionServicePlanApproval:
    async def test_non_admin_can_approve_plan(
        self,
        service: PermissionService,
    ) -> None:
        assert await service.may_approve("alice", "task-1", ApprovalType.PLAN)


class TestPermissionServiceExecutionApproval:
    async def test_non_admin_cannot_approve_execution(
        self,
        service: PermissionService,
    ) -> None:
        assert not await service.may_approve(
            "alice",
            "task-1",
            ApprovalType.EXECUTION,
        )

    async def test_admin_can_approve_execution(self) -> None:
        service = PermissionService(admins={"admin"}, human_approval_verified=True)
        assert await service.may_approve("admin", "task-1", ApprovalType.EXECUTION)

    async def test_admin_without_human_approval_proof_cannot_approve_execution(
        self,
        service: PermissionService,
    ) -> None:
        """#376: admin-list membership alone must not be enough for EXECUTION/

        MERGE. Without independent proof of a distinct human approval channel,
        a caller who merely knows/claims a listed admin name (exactly what a
        proposer sharing one secret with the approval endpoint could do) must
        still be denied.
        """
        assert not await service.may_approve("admin", "task-1", ApprovalType.EXECUTION)

    async def test_custom_admin_can_approve_execution(self) -> None:
        service = PermissionService(admins={"bob"}, human_approval_verified=True)
        assert await service.may_approve("bob", "task-1", ApprovalType.EXECUTION)

    async def test_default_fails_closed_without_config(self) -> None:
        """#226: no default hardcoded admin; empty config denies everyone."""
        from governance_controller.services.permission_service import (
            PermissionService,
        )

        service = PermissionService(admins=set(), human_approval_verified=True)
        assert not await service.may_approve(
            "admin",
            "task-1",
            ApprovalType.EXECUTION,
        )


class TestPermissionServiceMergeApproval:
    async def test_non_admin_cannot_approve_merge(
        self,
        service: PermissionService,
    ) -> None:
        assert not await service.may_approve("alice", "task-1", ApprovalType.MERGE)

    async def test_admin_can_approve_merge(self) -> None:
        service = PermissionService(admins={"admin"}, human_approval_verified=True)
        assert await service.may_approve("admin", "task-1", ApprovalType.MERGE)

    async def test_default_fails_closed_without_config_for_merge(self) -> None:
        """#226: empty admin config denies MERGE approvals too."""
        from governance_controller.services.permission_service import (
            PermissionService,
        )

        service = PermissionService(admins=set(), human_approval_verified=True)
        assert not await service.may_approve(
            "admin",
            "task-1",
            ApprovalType.MERGE,
        )


class TestPermissionServiceSystemAndAgent:
    async def test_system_cannot_approve_anything(
        self,
        service: PermissionService,
    ) -> None:
        for approval_type in ApprovalType:
            assert not await service.may_approve(
                "system",
                "task-1",
                approval_type,
            )

    async def test_agent_cannot_approve_anything(
        self,
        service: PermissionService,
    ) -> None:
        for approval_type in ApprovalType:
            assert not await service.may_approve(
                "agent",
                "task-1",
                approval_type,
            )

    async def test_system_prefixed_actor_cannot_approve(
        self,
        service: PermissionService,
    ) -> None:
        for approval_type in ApprovalType:
            assert not await service.may_approve(
                "system:macro-agent",
                "task-1",
                approval_type,
            )

    async def test_agent_prefixed_actor_cannot_approve(
        self,
        service: PermissionService,
    ) -> None:
        for approval_type in ApprovalType:
            assert not await service.may_approve(
                "agent:worker",
                "task-1",
                approval_type,
            )

    async def test_miscased_system_actor_blocked(
        self,
        service: PermissionService,
    ) -> None:
        """#227: 'System' and other case variants must be blocked."""
        for approval_type in ApprovalType:
            for actor in ("System", "SYSTEM", " Agent:worker ", "Agent:worker"):
                assert not await service.may_approve(
                    actor,
                    "task-1",
                    approval_type,
                )

    async def test_admin_matching_is_case_insensitive(
        self,
        service: PermissionService,
    ) -> None:
        """#227: configured admins match case-insensitively after normalization."""
        service = PermissionService(
            admins={"Alice@Example.com"}, human_approval_verified=True
        )
        assert await service.may_approve(
            "alice@example.com",
            "task-1",
            ApprovalType.EXECUTION,
        )


class TestWarnIfPermissionAllowlistsEmpty:
    """#388/#391: empty GC_ADMINS/GC_KNOWN_PROPOSERS/GC_HUMAN_APPROVAL_SECRET
    must not fail silently."""

    @staticmethod
    def _configure_structlog_for_caplog() -> None:
        # Route structlog through stdlib logging so caplog can see it.
        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.stdlib.render_to_log_kwargs,
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )

    def test_warns_when_both_are_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from governance_controller.config import settings

        monkeypatch.setattr(settings, "admins", "")
        monkeypatch.setattr(settings, "known_proposers", "")
        monkeypatch.setattr(settings, "human_approval_secret", "secret")
        self._configure_structlog_for_caplog()

        with caplog.at_level("WARNING"):
            warn_if_permission_allowlists_empty()

        matching = [
            r
            for r in caplog.records
            if "permission_allowlist_empty_fail_closed" in r.message
        ]
        assert matching, "expected a startup warning when both allow-lists are empty"
        assert matching[0].empty_settings == ["GC_ADMINS", "GC_KNOWN_PROPOSERS"]

    def test_warns_when_only_admins_is_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from governance_controller.config import settings

        monkeypatch.setattr(settings, "admins", "")
        monkeypatch.setattr(settings, "known_proposers", "agent-1")
        monkeypatch.setattr(settings, "human_approval_secret", "secret")
        self._configure_structlog_for_caplog()

        with caplog.at_level("WARNING"):
            warn_if_permission_allowlists_empty()

        matching = [
            r
            for r in caplog.records
            if "permission_allowlist_empty_fail_closed" in r.message
        ]
        assert matching
        assert matching[0].empty_settings == ["GC_ADMINS"]

    def test_warns_when_only_human_approval_secret_is_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """#391: GC_HUMAN_APPROVAL_SECRET must warn on its own, same as the
        other two fail-closed settings -- it is the one that #388/NEXT-17
        forgot to check."""
        from governance_controller.config import settings

        monkeypatch.setattr(settings, "admins", "admin@example.com")
        monkeypatch.setattr(settings, "known_proposers", "agent-1")
        monkeypatch.setattr(settings, "human_approval_secret", "")
        self._configure_structlog_for_caplog()

        with caplog.at_level("WARNING"):
            warn_if_permission_allowlists_empty()

        matching = [
            r
            for r in caplog.records
            if "permission_allowlist_empty_fail_closed" in r.message
        ]
        assert matching, (
            "expected a startup warning when GC_HUMAN_APPROVAL_SECRET is empty"
        )
        assert matching[0].empty_settings == ["GC_HUMAN_APPROVAL_SECRET"]

    def test_no_warning_when_all_are_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from governance_controller.config import settings

        monkeypatch.setattr(settings, "admins", "admin@example.com")
        monkeypatch.setattr(settings, "known_proposers", "agent-1")
        monkeypatch.setattr(settings, "human_approval_secret", "secret")
        self._configure_structlog_for_caplog()

        with caplog.at_level("WARNING"):
            warn_if_permission_allowlists_empty()

        assert not any(
            "permission_allowlist_empty_fail_closed" in r.message
            for r in caplog.records
        )
