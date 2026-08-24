"""Idea ingestion service: classify raw intake and create Plane drafts."""

import html

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.schemas.intake import ClassifiedIdea, RawIdea


class IdeaIngestionService:
    """Classify intake ideas and create draft Plane issues.

    Classification is intentionally rule-based for Phase 2; a future
    implementation may call an LLM for ambiguous items.

    Args:
        plane_client: Optional PlaneClient override.
    """

    def __init__(self, plane_client: PlaneClient | None = None) -> None:
        self._client = plane_client

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
            if token.startswith("proj-"):
                return ClassifiedIdea(
                    idea=idea,
                    category="existing_project",
                    project_id=token,
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
    ) -> dict[str, object] | None:
        """Create a Plane draft issue for a non-spam classified idea.

        Returns the Plane issue JSON or None if Plane is not configured or the
        idea is spam.
        """
        if classified.category == "spam":
            return None

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
        return await client.create_issue(
            name=title,
            description=description,
            extra={
                "source": classified.idea.source,
                "controller_status": "needs_triage",
            },
            project_id=effective_project,
        )
