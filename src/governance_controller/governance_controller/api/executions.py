"""Execution REST API endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db
from governance_controller.schemas.execution import ExecutionResponse

router = APIRouter(tags=["executions"])


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExecutionResponse:
    """Return an execution by its primary key.

    Phase 1: Execution records are not persisted yet; the endpoint returns
    HTTP 501 Not Implemented to keep the contract stable for future tasks.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Execution lookup is not implemented in Phase 1",
    )
