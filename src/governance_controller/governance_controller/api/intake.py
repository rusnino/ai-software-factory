"""Intake API endpoints for Telegram, Email, and generic ideas."""

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

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
    DuplicateIntakeError,
    IdeaIngestionService,
    IntakeRateLimitError,
)

router = APIRouter(prefix="/intake", tags=["intake"])


class IntakeAuthError(Exception):
    """Raised when an intake request fails authentication."""


def _validate_intake_secret(
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


def _release_intake_admission(request: Request) -> None:
    """Release the global IP token and charge the intake-specific IP budget."""
    release = getattr(request.state, "release_intake_rate_limit", None)
    if callable(release):
        release()
    charge = getattr(request.state, "charge_intake_ip_rate_limit", None)
    if callable(charge):
        charge()


def _require_intake_secret(
    request: Request,
    x_intake_secret: str | None = Header(default=None, alias="X-Intake-Secret"),
    x_intake_signature: str | None = Header(
        default=None, alias="X-Intake-Signature"
    ),
) -> None:
    """Validate shared intake authentication and release the IP token."""
    _validate_intake_secret(request, x_intake_secret, x_intake_signature)
    _release_intake_admission(request)


def _require_telegram_intake_secret(
    request: Request,
    x_intake_secret: str | None = Header(default=None, alias="X-Intake-Secret"),
    x_intake_signature: str | None = Header(
        default=None, alias="X-Intake-Signature"
    ),
) -> None:
    """Validate shared auth without releasing before Telegram auth runs."""
    _validate_intake_secret(request, x_intake_secret, x_intake_signature)


class _ServiceContainer:
    """Mutable container so tests can swap the ingestion service."""

    service: IdeaIngestionService | None = None


def get_idea_ingestion_service() -> IdeaIngestionService:
    """Return the default idea ingestion service."""
    if _ServiceContainer.service is not None:
        return _ServiceContainer.service
    return IdeaIngestionService()


def _raise_http_from_ingestion_error(exc: RuntimeError) -> None:
    """Translate ingestion service errors into explicit HTTP responses.

    Duplicate submissions become 409 Conflict so retry-on-error infra does not
    loop. Rate-limit violations become 429 Too Many Requests.
    """
    if isinstance(exc, DuplicateIntakeError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, IntakeRateLimitError):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    ) from exc


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
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ingestion: IdeaIngestionService = Depends(get_idea_ingestion_service),
    db: AsyncSession = Depends(get_db),
    _authenticated: None = Depends(_require_telegram_intake_secret),
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
    await run_in_threadpool(_release_intake_admission, request)

    message = TelegramAdapter._extract_message(update)
    text = TelegramAdapter._extract_text(message)

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

    try:
        draft = await ingestion.create_draft(classified, db=db)
    except RuntimeError as exc:
        _raise_http_from_ingestion_error(exc)
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

    try:
        draft = await ingestion.create_draft(classified, db=db)
    except RuntimeError as exc:
        _raise_http_from_ingestion_error(exc)
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

    try:
        draft = await ingestion.create_draft(classified, db=db)
    except RuntimeError as exc:
        _raise_http_from_ingestion_error(exc)
    return {
        "status": "draft_created",
        "category": classified.category,
        "plane_issue": draft,
    }
