"""Task REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.api.auth import require_controller_secret
from governance_controller.db import get_db
from governance_controller.models.task import Task
from governance_controller.schemas.task import TaskCreateRequest, TaskResponse
from governance_controller.services.task_service import TaskService


def _is_task_id_duplicate_error(exc: IntegrityError) -> bool:
    """Return True if *exc* is a conflict on the ``task`` table.

    ``IntegrityError`` can be raised by other tables (e.g. a race on
    ``project_profiles``). The response must only claim a duplicate task id
    when the conflict actually involves the task table.

    Drivers differ in what metadata they expose on the exception. SQLite sets
    ``orig.table_name``; asyncpg exposes the constraint name in the message.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None and getattr(orig, "table_name", None) == "task":
        return True
    msg = str(orig) if orig else str(exc)
    return ("task_pkey" in msg or "task." in msg) and "UNIQUE" in msg.upper()


router = APIRouter(tags=["tasks"])


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        state=task.state.value if hasattr(task.state, "value") else str(task.state),
        project_id=task.project_id,
        proposed_by=task.proposed_by,
        created_at=task.created_at,
        updated_at=task.updated_at,
        execution_attempts=task.execution_attempts,
    )


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(require_controller_secret),
) -> TaskResponse:
    """Create a governed task from a task contract and project profile.

    Requires ``X-Controller-Secret`` when ``GC_CONTROLLER_API_SECRET`` is
    configured.
    """
    service = TaskService(db)
    try:
        task = await service.create(
            task_contract=payload.task_contract,
            project_profile=payload.project_profile,
        )
    except IntegrityError as exc:
        await db.rollback()
        if _is_task_id_duplicate_error(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task with id {payload.task_contract.task_id} already exists",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected database conflict while creating task",
        ) from exc
    return _task_response(task)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Look up a task by its primary key."""
    service = TaskService(db)
    task = await service.get_by_id(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return _task_response(task)
