"""Email intake adapter.

Parses a normalized email payload into a RawIdea. Real mailbox fetching
(IMAP/POP3) is intentionally out of scope for Phase 2; the adapter expects
pre-fetched and validated email JSON.
"""

from typing import Any

from fastapi import HTTPException
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from governance_controller.schemas.intake import RawIdea


class EmailAdapter:
    """Translate a parsed email payload into a RawIdea."""

    # Mirrors RawIdea field constraints so failures surface as 422 instead of
    # leaking an uncaught pydantic.ValidationError as 500 (#356).
    _MAX_LENGTH: dict[str, int] = {
        "message_id": 256,
        "from": 256,
        "subject": 256,
        "body_text": 16384,
    }

    @staticmethod
    def _coerce(value: Any, field: str) -> str:
        """Return a cleaned string, raising 422 for malformed values (#258, #356)."""
        if not isinstance(value, str):
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Email field {field!r} must be a string, "
                    f"got {type(value).__name__}"
                ),
            )
        if not value:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Email field {field!r} must not be empty",
            )
        max_length = EmailAdapter._MAX_LENGTH.get(field, 16384)
        if len(value) > max_length:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Email field {field!r} exceeds maximum length "
                    f"{max_length}"
                ),
            )
        return value

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
