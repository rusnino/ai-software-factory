"""Execution REST API endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.models.execution import Execution
from governance_controller.schemas.execution import ExecutionResponse

router = APIRouter(tags=["executions"])


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExecutionResponse:
    """Return an execution by its primary key."""
    execution = await db.scalar(
        select(Execution).where(Execution.id == execution_id)  # type: ignore[arg-type]
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found",
        )

    return ExecutionResponse(
        id=execution.id,
        task_id=execution.task_id,
        macro_agent_run_id=execution.macro_agent_run_id,
        state=execution.state.value,
        started_at=execution.started_at,
        ended_at=execution.ended_at,
    )
