"""Tests for audit logging inside TaskService."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas import ProjectProfile, RepositoryConfig, TaskContract
from governance_controller.services.task_service import TaskService


async def test_task_creation_logs_task_created_event(
    db_session: AsyncSession,
) -> None:
    contract = TaskContract(
        task_id="audit-task-1",
        project_id="audit-proj-1",
        proposed_by="agent-1",
        objective="Build the thing",
        acceptance=["It works"],
    )
    profile = ProjectProfile(
        project_id="audit-proj-1",
        project_name="Audit Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )
    service = TaskService(db_session)

    await service.create(contract, profile)

    rows = await db_session.execute(
        select(AuditLog).where(AuditLog.task_id == "audit-task-1")
    )
    entries = rows.scalars().all()
    assert any(e.event_type == "task_created" for e in entries)
    assert any(e.event_type == "project_profile_created" for e in entries)


async def test_profile_update_logs_only_when_content_changes(
    db_session: AsyncSession,
) -> None:
    contract = TaskContract(
        task_id="audit-task-2",
        project_id="audit-proj-2",
        proposed_by="agent-1",
        objective="Build the thing",
        acceptance=["It works"],
    )
    profile = ProjectProfile(
        project_id="audit-proj-2",
        project_name="Audit Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )
    service = TaskService(db_session)

    await service.create(contract, profile)

    # Creating a second task with the same profile must not log an update.
    contract_2 = TaskContract(
        task_id="audit-task-3",
        project_id="audit-proj-2",
        proposed_by="agent-1",
        objective="Build another thing",
        acceptance=["It works"],
    )
    await service.create(contract_2, profile)
    unchanged_count = await _count_audit_events(db_session, "audit-task-3")
    assert unchanged_count == 1  # only task_created

    # Changing the profile must log an update.
    profile.project_name = "Renamed Project"
    contract_3 = TaskContract(
        task_id="audit-task-4",
        project_id="audit-proj-2",
        proposed_by="agent-1",
        objective="Build yet another thing",
        acceptance=["It works"],
    )
    await service.create(contract_3, profile)
    rows = await db_session.execute(
        select(AuditLog).where(AuditLog.task_id == "audit-task-4")
    )
    entries = rows.scalars().all()
    assert any(e.event_type == "task_created" for e in entries)
    assert any(e.event_type == "project_profile_updated" for e in entries)


async def _count_audit_events(db_session: AsyncSession, task_id: str) -> int:
    rows = await db_session.execute(select(AuditLog).where(AuditLog.task_id == task_id))
    return len(rows.scalars().all())


async def test_plane_issue_creation_failure_is_audited(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#215: a failed initial Plane issue creation must leave an audit trail."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")

    failing_service = MagicMock()
    failing_service.ensure_plane_issue = AsyncMock(
        side_effect=RuntimeError("Plane unavailable")
    )

    with patch(
        "governance_controller.services.task_service.PlaneProjectionService",
        return_value=failing_service,
    ):
        contract = TaskContract(
            task_id="audit-plane-fail",
            project_id="audit-proj-5",
            proposed_by="agent-1",
            objective="Build the thing",
            acceptance=["It works"],
        )
        profile = ProjectProfile(
            project_id="audit-proj-5",
            project_name="Audit Project",
            repository=RepositoryConfig(path="/tmp/repo"),
        )
        service = TaskService(db_session)
        await service.create(contract, profile)

    rows = await db_session.execute(
        select(AuditLog).where(AuditLog.task_id == "audit-plane-fail")
    )
    entries = rows.scalars().all()
    assert any(e.event_type == "plane_issue_creation_failed" for e in entries)


async def test_plane_issue_link_survives_caller_rollback(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link returned by Plane must be durable before the service returns."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")

    projection = MagicMock()
    projection.ensure_plane_issue = AsyncMock(return_value={"id": "plane-linked"})

    contract = TaskContract(
        task_id="audit-plane-link",
        project_id="audit-proj-link",
        proposed_by="agent-1",
        objective="Persist the Plane link",
        acceptance=["the link survives a caller rollback"],
    )
    profile = ProjectProfile(
        project_id="audit-proj-link",
        project_name="Project Link",
        repository=RepositoryConfig(path="/tmp/repo"),
    )

    with patch(
        "governance_controller.services.task_service.PlaneProjectionService",
        return_value=projection,
    ):
        await TaskService(db_session).create(contract, profile)

    # Simulate a caller failing after TaskService.create returned. The task and
    # Plane issue link must already be committed by the service itself.
    await db_session.rollback()

    task = await db_session.scalar(
        select(Task).where(Task.id == "audit-plane-link")
    )
    assert task is not None
    assert task.plane_issue_id == "plane-linked"


async def test_task_creation_persists_plane_projection_source(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plane retry metadata survives independently of the external projection."""
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    contract = TaskContract(
        task_id="audit-plane-source",
        project_id="audit-proj-source",
        proposed_by="agent-1",
        objective="Persist the Plane source",
        acceptance=["the source is durable"],
        approval_required=False,
    )
    profile = ProjectProfile(
        project_id="audit-proj-source",
        project_name="Source Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )

    task = await TaskService(db_session).create(
        contract,
        profile,
        source="telegram",
    )

    assert task.task_contract_json["_plane_projection_source"] == "telegram"
    assert task.task_contract_json["approval_required"] is False


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_task_creation_commits_before_slow_plane_projection(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#283: task/audit rows must be visible before Plane I/O begins."""
    from governance_controller import config

    class _SlowProjection:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def ensure_plane_issue(self, **_kwargs: object) -> dict[str, str]:
            self.started.set()
            await self.release.wait()
            return {"id": "plane-task-283"}

    _engine, session_local = isolated_db
    slow_projection = _SlowProjection()
    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")

    contract = TaskContract(
        task_id="task-create-283",
        project_id="project-create-283",
        proposed_by="agent-1",
        objective="Test task creation transaction boundary",
        acceptance=["the audit is durable before projection"],
    )
    profile = ProjectProfile(
        project_id="project-create-283",
        project_name="Project 283",
        repository=RepositoryConfig(path="/tmp/repo"),
    )

    creator = session_local()
    with patch(
        "governance_controller.services.task_service.PlaneProjectionService",
        return_value=slow_projection,
    ):
        create_task = asyncio.create_task(
            TaskService(creator).create(contract, profile)
        )
        try:
            await slow_projection.started.wait()
            async with session_local() as observer:
                task_row = await observer.scalar(
                    select(Task).where(Task.id == contract.task_id)
                )
                audit_rows = await observer.execute(
                    select(AuditLog).where(AuditLog.task_id == contract.task_id)
                )

            assert task_row is not None
            assert any(
                row.event_type == "task_created"
                for row in audit_rows.scalars().all()
            )
        finally:
            slow_projection.release.set()
            await create_task
            await creator.rollback()
            await creator.close()
