"""Intake API endpoints for Telegram, Email, and generic ideas."""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from governance_controller.adapters.email import EmailAdapter
from governance_controller.adapters.telegram import (
    TelegramAdapter,
    TelegramWebhookAuthError,
)
from governance_controller.schemas.intake import RawIdea
from governance_controller.services.idea_ingestion_service import (
    IdeaIngestionService,
)

router = APIRouter(prefix="/intake", tags=["intake"])


class _ServiceContainer:
    """Mutable container so tests can swap the ingestion service."""

    service: IdeaIngestionService | None = None


def get_idea_ingestion_service() -> IdeaIngestionService:
    """Return the default idea ingestion service."""
    if _ServiceContainer.service is not None:
        return _ServiceContainer.service
    return IdeaIngestionService()


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_intake(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ingestion: IdeaIngestionService = Depends(get_idea_ingestion_service),
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
) -> dict[str, Any]:
    """Receive a parsed email payload and create a Plane draft if relevant."""
    idea = EmailAdapter.parse(payload)
    classified = ingestion.classify(idea)
    if classified.category == "spam":
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
) -> dict[str, Any]:
    """Receive a generic normalized idea and create a Plane draft."""
    classified = ingestion.classify(idea)
    if classified.category == "spam":
        return {"status": "ignored", "reason": classified.reason}

    draft = await ingestion.create_draft(classified)
    return {
        "status": "draft_created",
        "category": classified.category,
        "plane_issue": draft,
    }
