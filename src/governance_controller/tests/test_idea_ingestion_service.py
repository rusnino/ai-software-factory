"""Direct tests for IdeaIngestionService classify/create_draft logic."""

from typing import Any

from governance_controller.schemas.intake import RawIdea
from governance_controller.services.idea_ingestion_service import (
    IdeaIngestionService,
)


class _FakePlaneClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_issue(
        self,
        name: str,
        description: str | None = None,
        state: str | None = None,
        project_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "description": description,
                "project_id": project_id,
                "extra": extra,
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
    classified = service.classify(_idea(body="Need this in proj-alpha please"))

    assert classified.category == "existing_project"
    assert classified.project_id == "proj-alpha"


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
    assert call["extra"]["controller_status"] == "needs_triage"


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
