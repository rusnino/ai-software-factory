"""Approval REST API endpoint."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.api.auth import (
    require_controller_secret,
)
from governance_controller.config import settings
from governance_controller.constants import ApprovalType
from governance_controller.db import get_db
from governance_controller.schemas.approval import ApprovalRequest, ApprovalResponse
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.permission_service import PermissionService
from governance_controller.services.policy_engine import PolicyViolationError
from governance_controller.services.task_service import TaskService

router = APIRouter(tags=["approvals"])


def get_approval_service(
    db: AsyncSession = Depends(get_db),
    x_human_approval_secret: str | None = Header(
        default=None, alias="X-Human-Approval-Secret"
    ),
) -> ApprovalService:
    """Build the approval service; override in tests to inject mocks.

    Resolves whether this specific request proves a genuinely distinct,
    human-controlled approval channel (#376): `X-Human-Approval-Secret` must
    be configured and match, independent of whatever secret authenticated
    task creation. Without this, `known_proposers`/`admins` are just name
    allow-lists that a single caller holding only `X-Controller-Secret`
    could satisfy alone by proposing as one known name and approving as
    another.
    """
    configured = settings.human_approval_secret
    human_approval_verified = bool(configured) and hmac.compare_digest(
        x_human_approval_secret or "", configured
    )
    return ApprovalService(
        db=db,
        permission_service=PermissionService(
            human_approval_verified=human_approval_verified
        ),
    )


def _make_idempotency_key(
    task_id: str, approval_type: ApprovalType, actor: str, timestamp: str
) -> str:
    """Round timestamp to seconds and build a deterministic idempotency key."""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp format: {timestamp}") from exc
    rounded = parsed.replace(microsecond=0)
    if rounded.tzinfo is None:
        rounded = rounded.replace(tzinfo=UTC)
    # Hash a JSON-encoded tuple so caller-controlled values cannot collide by
    # embedding the delimiter (e.g. task_id "a|b" / actor "c" vs task_id "a" /
    # actor "b|c"). Key length stays fixed regardless of component lengths.
    components = (task_id, approval_type.value, actor, rounded.isoformat())
    canonical = json.dumps(components, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/approvals")
async def submit_approval(
    payload: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    approval_service: ApprovalService = Depends(get_approval_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _authenticated: None = Depends(require_controller_secret),
) -> ApprovalResponse:
    """Single authoritative approval endpoint.

    Validates policy, advances state, and records the approval. Supports an
    optional ``Idempotency-Key`` header; when absent, a deterministic fallback
    key is derived from ``(task_id, approval_type, actor, timestamp)``. That
    fallback key is always computed (even when the header is present) so a
    later retry that adds an explicit key still correlates to a prior
    fallback-keyed request for the same logical approval (FR-23a).

    Requires ``X-Controller-Secret`` when ``GC_CONTROLLER_API_SECRET`` is
    configured. EXECUTION and MERGE approvals additionally require
    ``X-Human-Approval-Secret`` to match ``GC_HUMAN_APPROVAL_SECRET`` (#376).
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

    # Always derive the deterministic fallback key, even when the caller sent
    # an explicit header: a retry that adds `Idempotency-Key` after an initial
    # request went out without one must still correlate to that request's
    # stored fallback key (FR-23a / SPEC-03 §3.3), or the retry misreads a
    # already-succeeded approval as a fresh, conflicting transition (409).
    try:
        fallback_idempotency_key = _make_idempotency_key(
            payload.task_id,
            payload.approval_type,
            payload.actor,
            payload.timestamp,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if idempotency_key is None:
        idempotency_key = fallback_idempotency_key

    try:
        updated_task = await approval_service.approve(
            task=task,
            contract=contract,
            profile=project_profile,
            approval_type=payload.approval_type,
            source=payload.source,
            actor=payload.actor,
            idempotency_key=idempotency_key,
            fallback_idempotency_key=fallback_idempotency_key,
            comment=payload.comment,
        )
    except PolicyViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": str(exc), "violations": exc.violations},
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if "Invalid transition" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        if "Concurrent modification" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
        ) from exc
    except RuntimeError as exc:
        # Downstream executor (macro-agent) startup failure.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ApprovalResponse(
        task_id=updated_task.id,
        state=updated_task.state.value,
        approved=True,
    )
