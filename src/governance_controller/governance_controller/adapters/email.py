"""Email intake adapter.

Parses a normalized email payload into a RawIdea. Real mailbox fetching
(IMAP/POP3) is intentionally out of scope for Phase 2; the adapter expects
pre-fetched and validated email JSON.
"""

from typing import Any

from fastapi import HTTPException, status

from governance_controller.schemas.intake import RawIdea


class EmailAdapter:
    """Translate a parsed email payload into a RawIdea."""

    @staticmethod
    def _coerce(value: Any, field: str) -> str:
        """Return a cleaned string, raising 422 for non-string values (#258)."""
        if isinstance(value, str):
            return value
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Email field {field!r} must be a string, "
                f"got {type(value).__name__}"
            ),
        )

    @staticmethod
    def parse(payload: dict[str, Any]) -> RawIdea:
        """Create a RawIdea from an email-like dict.

        Expected keys: message_id, from, subject, body_text.
        """
        sender = payload.get("from", "unknown")
        if isinstance(sender, dict):
            sender = sender.get("address", "unknown")
        return RawIdea(
            source="email",
            source_id=EmailAdapter._coerce(
                payload.get("message_id", "email-unknown"), "message_id"
            ),
            sender=EmailAdapter._coerce(sender, "from"),
            subject=EmailAdapter._coerce(
                payload.get("subject", "No subject"), "subject"
            ),
            body=EmailAdapter._coerce(payload.get("body_text", ""), "body_text"),
        )
