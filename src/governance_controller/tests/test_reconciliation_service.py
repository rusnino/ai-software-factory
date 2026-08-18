"""Tests for the reconciliation service."""

from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_adapter_memory import MemoryPlaneAdapter
from governance_controller.constants import TaskState
from governance_controller.models.task import Task
from governance_controller.services.reconciliation_service import (
    ReconciliationService,
)


async def test_reconciliation_reports_match(db_session: AsyncSession) -> None:
    task = Task(id="rec-task-1", project_id="proj-1", proposed_by="agent-1")
    db_session.add(task)
    await db_session.flush()

    adapter = MemoryPlaneAdapter()
    adapter.sync_task_state("rec-task-1", TaskState.PROPOSED.value)
    service = ReconciliationService(db=db_session, plane_adapter=adapter)

    report = await service.check("rec-task-1")

    assert report["diverged"] is False
    assert report["resolved"] is True


async def test_reconciliation_reports_divergence(db_session: AsyncSession) -> None:
    task = Task(id="rec-task-2", project_id="proj-1", proposed_by="agent-1")
    db_session.add(task)
    await db_session.flush()

    adapter = MemoryPlaneAdapter()
    adapter.sync_task_state("rec-task-2", TaskState.RUNNING.value)
    service = ReconciliationService(db=db_session, plane_adapter=adapter)

    report = await service.check("rec-task-2")

    assert report["diverged"] is True
    assert report["resolved"] is False
    assert report["controller_state"] == TaskState.PROPOSED.value
    assert report["plane_state"] == TaskState.RUNNING.value
