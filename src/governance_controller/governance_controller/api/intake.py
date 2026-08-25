"""Intake API endpoints for Telegram, Email, and generic ideas."""

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.email import EmailAdapter
from governance_controller.adapters.telegram import (
    TelegramAdapter,
    TelegramWebhookAuthError,
)
from governance_controller.config import settings
from governance_controller.db import get_db
from governance_controller.schemas.intake import RawIdea
from governance_controller.services.audit_service import AuditService
from governance_controller.services.idea_ingestion_service import (
    IdeaIngestionService,
)

router = APIRouter(prefix="/intake", tags=["intake"])


class IntakeAuthError(Exception):
    """Raised when an intake request fails authentication."""


def _require_intake_secret(
    request: Request,
    x_intake_secret: str | None = Header(default=None, alias="X-Intake-Secret"),
    x_intake_signature: str | None = Header(
        default=None, alias="X-Intake-Signature"
    ),
) -> None:
    """Validate intake webhooks using a shared secret or HMAC signature.

    The intake endpoints are closed by default. Email/webhook providers
    typically sign the request body with a shared secret rather than sending
    the secret in a header. When ``X-Intake-Signature`` is present we verify
    the HMAC-SHA256 hex digest of the body against ``settings.intake_secret``.
    Otherwise we fall back to the legacy ``X-Intake-Secret`` header comparison.
    """
    configured = settings.intake_secret
    if not configured:
        raise IntakeAuthError("Intake secret is not configured")

    if x_intake_signature is not None:
        body = getattr(request.state, "raw_body", b"")
        expected = hmac.new(
            configured.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(x_intake_signature, expected):
            raise IntakeAuthError("Invalid intake signature")
        return

    if not hmac.compare_digest(x_intake_secret or "", configured):
        raise IntakeAuthError("Invalid or missing intake secret")


class _ServiceContainer:
    """Mutable container so tests can swap the ingestion service."""

    service: IdeaIngestionService | None = None


def get_idea_ingestion_service() -> IdeaIngestionService:
    """Return the default idea ingestion service."""
    if _ServiceContainer.service is not None:
        return _ServiceContainer.service
    return IdeaIngestionService()


async def _log_spam(
    db: AsyncSession,
    idea: RawIdea,
    classified_reason: str,
) -> None:
    """Record an audit entry when intake filters out a message as spam."""
    await AuditService.log(
        db=db,
        event_type="intake_spam_filtered",
        task_id="",
        actor=idea.sender,
        source=f"intake:{idea.source}",
        payload={
            "source_id": idea.source_id,
            "reason": classified_reason,
            "subject": idea.subject,
        },
    )


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_intake(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ingestion: IdeaIngestionService = Depends(get_idea_ingestion_service),
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_intake_secret),
) -> dict[str, Any]:
    """Receive a Telegram update.

    /approve commands are forwarded to the approvals endpoint as before.
      Other text messages are treated as intake ideas, classified, and used to
    create a Plane draft issue when Plane is configured.
    """
    adapter = TelegramAdapter()
    try:
        adapter.authenticate_update(secret_token_header=x_telegram_bot_api_secret_token)
    except TelegramWebhookAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    message = update.get("message") or {}
    text = message.get("text") or ""

    if text.startswith("/approve"):
        result = await adapter.process_update(
            update,
            secret_token_header=x_telegram_bot_api_secret_token,
        )
        return result

    idea = RawIdea(
        source="telegram",
        source_id=str(message.get("message_id", "telegram-unknown")),
        sender=adapter.derive_actor(message),
        subject=text[:120],
        body=text,
    )
    classified = ingestion.classify(idea)
    if classified.category == "spam":
        await _log_spam(db, idea, classified.reason)
        return {"status": "ignored", "reason": classified.reason}

    draft = await ingestion.create_draft(classified)
    return {
        "status": "draft_created",
        "category": classified.category,
        "plane_issue": draft,
    }


@router.post("/email", status_code=status.HTTP_200_OK)
async def email_intake(
    payload: dict[str, Any],
    ingestion: IdeaIngestionService = Depends(get_idea_ingestion_service),
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_intake_secret),
) -> dict[str, Any]:
    """Receive a parsed email payload and create a Plane draft if relevant."""
    idea = EmailAdapter.parse(payload)
    classified = ingestion.classify(idea)
    if classified.category == "spam":
        await _log_spam(db, idea, classified.reason)
        return {"status": "ignored", "reason": classified.reason}

    draft = await ingestion.create_draft(classified)
    return {
        "status": "draft_created",
        "category": classified.category,
        "plane_issue": draft,
    }


@router.post("/idea", status_code=status.HTTP_200_OK)
async def generic_idea_intake(
    idea: RawIdea,
    ingestion: IdeaIngestionService = Depends(get_idea_ingestion_service),
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_intake_secret),
) -> dict[str, Any]:
    """Receive a generic normalized idea and create a Plane draft."""
    classified = ingestion.classify(idea)
    if classified.category == "spam":
        await _log_spam(db, idea, classified.reason)
        return {"status": "ignored", "reason": classified.reason}

    draft = await ingestion.create_draft(classified)
    return {
        "status": "draft_created",
        "category": classified.category,
        "plane_issue": draft,
    }
