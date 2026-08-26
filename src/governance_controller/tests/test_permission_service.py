"""Tests for the PermissionService stub."""

import pytest

from governance_controller.constants import ApprovalType
from governance_controller.services.permission_service import PermissionService


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

    async def test_admin_can_approve_execution(
        self,
        service: PermissionService,
    ) -> None:
        assert await service.may_approve("admin", "task-1", ApprovalType.EXECUTION)

    async def test_custom_admin_can_approve_execution(self) -> None:
        service = PermissionService(admins={"bob"})
        assert await service.may_approve("bob", "task-1", ApprovalType.EXECUTION)

    async def test_default_fails_closed_without_config(self) -> None:
        """#226: no default hardcoded admin; empty config denies everyone."""
        from governance_controller.services.permission_service import (
            PermissionService,
        )

        service = PermissionService(admins=set())
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

    async def test_admin_can_approve_merge(
        self,
        service: PermissionService,
    ) -> None:
        assert await service.may_approve("admin", "task-1", ApprovalType.MERGE)

    async def test_default_fails_closed_without_config_for_merge(self) -> None:
        """#226: empty admin config denies MERGE approvals too."""
        from governance_controller.services.permission_service import (
            PermissionService,
        )

        service = PermissionService(admins=set())
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
        service = PermissionService(admins={"Alice@Example.com"})
        assert await service.may_approve(
            "alice@example.com",
            "task-1",
            ApprovalType.EXECUTION,
        )
