"""Tests for the PermissionService stub."""

import pytest

from governance_controller.constants import ApprovalType
from governance_controller.services.permission_service import PermissionService


@pytest.fixture
def service() -> PermissionService:
    return PermissionService()


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
