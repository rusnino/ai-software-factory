"""Task REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.schemas.task import TaskCreateRequest, TaskResponse
from governance_controller.services.task_service import TaskService

router = APIRouter(tags=["tasks"])


def _task_response(task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        state=task.state.value if hasattr(task.state, "value") else str(task.state),
        project_id=task.project_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Create a governed task from a task contract and project profile."""
    service = TaskService(db)
    task = await service.create(
        task_contract=payload.task_contract,
        project_profile=payload.project_profile,
    )
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
