"""Tests for audit logging inside TaskService."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.audit_log import AuditLog
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
    rows = await db_session.execute(
        select(AuditLog).where(AuditLog.task_id == task_id)
    )
    return len(rows.scalars().all())
