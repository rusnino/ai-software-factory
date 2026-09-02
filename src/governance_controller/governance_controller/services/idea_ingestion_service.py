"""Idea ingestion service: classify raw intake and create Plane drafts."""

import hashlib
import html
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.models.intake_submission import IntakeSubmission
from governance_controller.schemas.intake import ClassifiedIdea, RawIdea

# Plane project ids are UUIDs. The intake parser only accepts tokens that
# match this strict pattern so free-text senders cannot redirect Plane writes.
_PROJECT_ID_RE = re.compile(
    r"^proj-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class DuplicateIntakeError(RuntimeError):
    """Raised when the same intake submission is received twice."""


class IntakeRateLimitError(RuntimeError):
    """Raised when a sender exceeds the intake rate limit."""


class IdeaIngestionService:
    """Classify intake ideas and create draft Plane issues.

    Classification is intentionally rule-based for Phase 2; a future
    implementation may call an LLM for ambiguous items.

    Args:
        plane_client: Optional PlaneClient override.
    """

    def __init__(self, plane_client: PlaneClient | None = None) -> None:
        self._client = plane_client

    @staticmethod
    def _normalize_sender(sender: str) -> str:
        """Return the quota identity shared by case/whitespace variants."""
        return sender.strip().casefold()

    @staticmethod
    def _sender_lock_key(sender: str) -> int:
        """Return a stable signed 64-bit PostgreSQL advisory-lock key."""
        digest = hashlib.sha256(
            b"intake-sender:" + sender.encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    def classify(self, idea: RawIdea) -> ClassifiedIdea:
        """Return a classified idea.

        Current rules:
        - Empty/spam keywords -> spam.
        - Mentions an existing project id pattern (proj-*) -> existing_project.
        - Otherwise -> new_project (with low confidence).
        """
        lower = (idea.subject + " " + idea.body).lower()
        spam_words = {"buy now", "click here", "unsubscribe", "lottery"}
        if any(word in lower for word in spam_words) or not idea.body.strip():
            return ClassifiedIdea(
                idea=idea,
                category="spam",
                confidence=1.0,
                reason="Spam indicators detected",
            )

        for token in lower.split():
            if _PROJECT_ID_RE.match(token):
                return ClassifiedIdea(
                    idea=idea,
                    category="existing_project",
                    project_id=token.lower(),
                    confidence=0.7,
                    reason=f"Referenced project {token}",
                )

        return ClassifiedIdea(
            idea=idea,
            category="new_project",
            confidence=0.5,
            reason="No project reference found; treating as new",
        )

    async def create_draft(
        self,
        classified: ClassifiedIdea,
        project_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict[str, object] | None:
        """Create a Plane draft issue for a non-spam classified idea.

        Returns the Plane issue JSON or None if Plane is not configured or the
        idea is spam. When ``db`` is provided, duplicate submissions and sender
        rate limits are enforced and the submission is recorded before any
        Plane call, so the guards remain effective even when Plane is disabled
        (#256).
        """
        if classified.category == "spam":
            return None

        if db is not None:
            sender = self._normalize_sender(classified.idea.sender)
            await self._guard_duplicate_and_rate_limit(
                db,
                classified.idea.source,
                classified.idea.source_id,
                sender,
            )
            # Persist the submission unconditionally. The row must exist for
            # duplicate/rate-limit enforcement even if Plane is disabled or its
            # call fails. The unique index on (source, source_id) is the final
            # backstop against races that pass the SELECT-based pre-check; a
            # duplicate-key failure is translated to the same DuplicateIntakeError
            # so callers get a clean 409 instead of a 500 (#274).
            db.add(
                IntakeSubmission(
                    source=classified.idea.source,
                    source_id=classified.idea.source_id,
                    sender=sender,
                )
            )
            try:
                await db.flush()
            except IntegrityError as exc:
                raise DuplicateIntakeError(
                    f"Duplicate intake submission: "
                    f"{classified.idea.source}/{classified.idea.source_id}"
                ) from exc
            # Commit the guard record before non-authoritative Plane I/O. The
            # request dependency rolls back on Plane failures, so a flush alone
            # would erase the duplicate and sender-rate-limit history (#256).
            await db.commit()

        client = self._client
        if client is None:
            if not settings.plane_base_url:
                return None
            client = PlaneClient()

        effective_project = project_id or classified.project_id
        if effective_project is None:
            effective_project = getattr(settings, "plane_project_id", None)
        if effective_project is None:
            raise RuntimeError("No Plane project id configured for draft creation")

        title = html.escape(classified.idea.subject or "Intake draft")
        description = html.escape(classified.idea.body)
        # Intake-only metadata has no supported Plane create-field contract.
        result = await client.create_issue(
            name=title,
            description=description,
            project_id=effective_project,
        )

        return result

    async def _guard_duplicate_and_rate_limit(
        self,
        db: AsyncSession,
        source: str,
        source_id: str,
        sender: str,
    ) -> None:
        """Raise RuntimeError if the intake request is a duplicate or over limit."""
        bind = db.bind
        if bind is not None and bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": self._sender_lock_key(sender)},
            )

        existing = await db.scalar(
            select(IntakeSubmission).where(
                IntakeSubmission.source == source,  # type: ignore[arg-type]
                IntakeSubmission.source_id == source_id,  # type: ignore[arg-type]
            )
        )
        if existing is not None:
            raise DuplicateIntakeError(
                f"Duplicate intake submission: {source}/{source_id}"
            )

        limit = settings.intake_rate_limit_per_minute
        if limit <= 0:
            return

        since = datetime.now(UTC) - timedelta(minutes=1)
        count_result = await db.execute(
            select(func.count(IntakeSubmission.id)).where(  # type: ignore[arg-type]
                IntakeSubmission.sender == sender,  # type: ignore[arg-type]
                IntakeSubmission.created_at >= since,  # type: ignore[arg-type]
            )
        )
        recent = count_result.scalar() or 0
        if recent >= limit:
            raise IntakeRateLimitError(
                f"Rate limit exceeded for sender {sender}: {recent} in the last minute"
            )
