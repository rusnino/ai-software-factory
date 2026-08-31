"""Direct tests for IdeaIngestionService classify/create_draft logic."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.intake_submission import IntakeSubmission
from governance_controller.schemas.intake import RawIdea
from governance_controller.services.idea_ingestion_service import (
    DuplicateIntakeError,
    IdeaIngestionService,
)


class _FakePlaneClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_issue(
        self,
        name: str,
        description: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "description": description,
                "project_id": project_id,
            }
        )
        return {"id": "issue-1", "name": name}


def _idea(**kwargs: Any) -> RawIdea:
    defaults = {
        "source": "email",
        "source_id": "em-1",
        "sender": "a@b.com",
        "subject": "Feature request",
        "body": "Build dark mode",
    }
    defaults.update(kwargs)
    return RawIdea(**defaults)


def test_classify_detects_spam() -> None:
    service = IdeaIngestionService()
    classified = service.classify(_idea(body="buy now cheap viagra"))

    assert classified.category == "spam"
    assert classified.confidence == 1.0


def test_classify_detects_empty_body_as_spam() -> None:
    service = IdeaIngestionService()
    classified = service.classify(_idea(body="   "))

    assert classified.category == "spam"


def test_classify_detects_existing_project() -> None:
    service = IdeaIngestionService()
    project_uuid = str(uuid.uuid4())
    classified = service.classify(
        _idea(body=f"Need this in proj-{project_uuid} please")
    )

    assert classified.category == "existing_project"
    assert classified.project_id == f"proj-{project_uuid}"


def test_classify_ignores_malformed_project_token() -> None:
    service = IdeaIngestionService()
    classified = service.classify(
        _idea(body="Need this in proj-123/../../../evil please")
    )

    assert classified.category == "new_project"


def test_classify_defaults_to_new_project() -> None:
    service = IdeaIngestionService()
    classified = service.classify(_idea(body="A totally new idea"))

    assert classified.category == "new_project"


async def test_create_draft_skips_spam() -> None:
    service = IdeaIngestionService(plane_client=_FakePlaneClient())
    classified = service.classify(_idea(body="buy now"))

    result = await service.create_draft(classified, project_id="proj-1")
    assert result is None


async def test_create_draft_creates_plane_issue() -> None:
    fake_client = _FakePlaneClient()
    service = IdeaIngestionService(plane_client=fake_client)
    classified = service.classify(_idea(subject="Dark mode", body="Make it dark"))

    result = await service.create_draft(classified, project_id="proj-1")

    assert result is not None
    assert result["id"] == "issue-1"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["name"] == "Dark mode"
    assert call["description"] == "Make it dark"
    assert call["project_id"] == "proj-1"
    assert set(call) == {"name", "description", "project_id"}


async def test_create_draft_escapes_html_in_plane_payload() -> None:
    fake_client = _FakePlaneClient()
    service = IdeaIngestionService(plane_client=fake_client)
    classified = service.classify(
        _idea(subject="<b>bold</b>", body="<script>xss</script>")
    )

    await service.create_draft(classified, project_id="proj-1")

    call = fake_client.calls[0]
    assert "<b>" not in call["name"]
    assert "&lt;b&gt;" in call["name"]
    assert "<script>" not in call["description"]
    assert "&lt;script&gt;" in call["description"]


async def test_intake_submission_survives_plane_failure_and_request_rollback(
    db_session: AsyncSession,
) -> None:
    """#256: a Plane outage must not erase duplicate protection."""

    class _FailingPlaneClient:
        calls = 0

        async def create_issue(
            self,
            name: str,
            description: str | None = None,
            project_id: str | None = None,
        ) -> dict[str, Any]:
            self.calls += 1
            raise RuntimeError("Plane unavailable")

    failing_client = _FailingPlaneClient()
    service = IdeaIngestionService(plane_client=failing_client)
    classified = service.classify(_idea(source_id="plane-down-256"))

    with pytest.raises(RuntimeError, match="Plane unavailable"):
        await service.create_draft(classified, project_id="proj-1", db=db_session)
    await db_session.rollback()

    rows = await db_session.execute(
        select(IntakeSubmission).where(
            IntakeSubmission.source == "email",
            IntakeSubmission.source_id == "plane-down-256",
        )
    )
    assert len(rows.scalars().all()) == 1

    with pytest.raises(DuplicateIntakeError):
        await service.create_draft(classified, project_id="proj-1", db=db_session)
    assert failing_client.calls == 1


async def test_create_draft_translates_race_lost_integrity_error_to_duplicate(
    db_session: AsyncSession,
) -> None:
    """#274: a concurrent duplicate that races past the pre-check SELECT must
    still surface as DuplicateIntakeError, not a raw IntegrityError.

    Simulates two requests that both pass ``_guard_duplicate_and_rate_limit``'s
    SELECT-based pre-check before either commits (the actual TOCTOU race): a
    committed ``IntakeSubmission`` row already exists for this
    (source, source_id) pair, but the pre-check is monkeypatched to a no-op —
    exactly as if THIS call's own pre-check had already run and passed before
    the other request's INSERT landed. The real ``db.flush()`` then hits the
    genuine unique-constraint violation, which must be caught and translated,
    not propagate as a raw ``IntegrityError``.
    """
    fake_client = _FakePlaneClient()
    service = IdeaIngestionService(plane_client=fake_client)

    db_session.add(
        IntakeSubmission(source="email", source_id="dup-274", sender="a@b.com")
    )
    await db_session.commit()

    classified = service.classify(
        _idea(source="email", source_id="dup-274", body="Racing duplicate idea")
    )

    async def _skip_precheck(*args: Any, **kwargs: Any) -> None:
        return None

    service._guard_duplicate_and_rate_limit = _skip_precheck  # type: ignore[method-assign]

    with pytest.raises(DuplicateIntakeError):
        await service.create_draft(classified, project_id="proj-1", db=db_session)

    # Production code relies on get_db() to roll back the aborted Postgres
    # transaction after the caught IntegrityError; this test drives
    # create_draft() directly, so it must do the same before querying again.
    await db_session.rollback()

    # No Plane draft was created for the lost racer, and only the original
    # row exists — no duplicate committed.
    assert fake_client.calls == []
    rows = await db_session.execute(
        select(IntakeSubmission).where(
            IntakeSubmission.source == "email",
            IntakeSubmission.source_id == "dup-274",
        )
    )
    assert len(rows.scalars().all()) == 1
