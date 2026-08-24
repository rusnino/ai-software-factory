"""Email intake adapter.

Parses a normalized email payload into a RawIdea. Real mailbox fetching
(IMAP/POP3) is intentionally out of scope for Phase 2; the adapter expects
pre-fetched and validated email JSON.
"""

from typing import Any

from governance_controller.schemas.intake import RawIdea


class EmailAdapter:
    """Translate a parsed email payload into a RawIdea."""

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
            source_id=payload.get("message_id", "email-unknown"),
            sender=str(sender),
            subject=payload.get("subject", "No subject"),
            body=payload.get("body_text", ""),
        )
