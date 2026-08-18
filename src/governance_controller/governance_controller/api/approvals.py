"""Approval REST API endpoint."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.constants import ApprovalType
from governance_controller.db import get_db
from governance_controller.schemas.approval import ApprovalRequest, ApprovalResponse
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.task_service import TaskService

router = APIRouter(tags=["approvals"])


def _make_idempotency_key(
    task_id: str, approval_type: ApprovalType, actor: str, timestamp: str
) -> str:
    """Round timestamp to seconds and build a deterministic idempotency key."""
    parsed = datetime.fromisoformat(timestamp)
    rounded = parsed.replace(microsecond=0)
    if rounded.tzinfo is None:
        rounded = rounded.replace(tzinfo=UTC)
    components = (
        task_id,
        approval_type.value,
        actor,
        rounded.isoformat(),
    )
    return "|".join(components)


@router.post("/approvals")
async def submit_approval(
    payload: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Single authoritative approval endpoint.

    Validates policy, advances state, and records the approval.
    """
    task_service = TaskService(db)

    task = await task_service.get_by_id(payload.task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {payload.task_id} not found",
        )

    project_profile = await task_service.get_profile_by_project_id(task.project_id)
    if project_profile is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Project profile not found for task",
        )

    try:
        task_contract_data: dict[str, Any] = task.task_contract_json
        contract = TaskContract(**task_contract_data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored task contract is invalid: {exc}",
        ) from exc

    idempotency_key = _make_idempotency_key(
        payload.task_id,
        payload.approval_type,
        payload.actor,
        payload.timestamp,
    )

    approval_service = ApprovalService(db)

    try:
        updated_task = await approval_service.approve(
            task=task,
            contract=contract,
            profile=project_profile,
            approval_type=payload.approval_type,
            source=payload.source,
            actor=payload.actor,
            idempotency_key=idempotency_key,
            comment=payload.comment,
        )
    except ValueError as exc:
        message = str(exc)
        if "Invalid transition" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        if "Policy violation" in message:
            # Reconstruct violations from the ValueError message for the response.
            prefix = "Policy violation(s): "
            violations = []
            if message.startswith(prefix):
                violations = [
                    v.strip() for v in message[len(prefix) :].split(",") if v.strip()
                ]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": message, "violations": violations},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
        ) from exc

    return ApprovalResponse(
        task_id=updated_task.id,
        state=updated_task.state.value,
        approved=True,
    )
