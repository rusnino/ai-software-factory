"""Telegram inbound approval adapter.

This adapter translates Telegram update payloads into Controller approval
requests. It performs no polling/long-polling; it is intended to be invoked
from a webhook handler or future polling loop.

Webhook authentication is defensive: callers must pass the secret token
header if one is configured. Missing/invalid tokens produce a ``403`` so
that unauthenticated updates cannot reach ``POST /approvals``.
"""

import hmac
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from governance_controller.config import settings
from governance_controller.constants import ApprovalType
from governance_controller.schemas.approval import ApprovalRequest


class TelegramWebhookAuthError(Exception):
    """Raised when a Telegram update fails webhook authentication."""


class TelegramAdapter:
    """Inbound Telegram /approve command adapter with sender attribution."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        secret_token: str | None = None,
    ) -> None:
        self.base_url = base_url
        configured_secret = secret_token or settings.telegram_webhook_secret_token
        self.secret_token = configured_secret if configured_secret else None

    def authenticate_update(self, *, secret_token_header: str | None = None) -> None:
        """Validate the Telegram secret-token header when configured.

        Telegram sends the configured ``secret_token`` in the
        ``X-Telegram-Bot-Api-Secret-Token`` header. If the adapter has a
        non-empty secret configured, only updates presenting the exact same
        value are accepted. Raises ``TelegramWebhookAuthError`` when
        authentication fails.

        If no secret is configured the endpoint is closed by default; this
        prevents accidentally exposing an unauthenticated webhook in
        production.
        """
        if self.secret_token is None:
            raise TelegramWebhookAuthError(
                "Telegram webhook secret is not configured"
            )
        if not hmac.compare_digest(secret_token_header or "", self.secret_token):
            raise TelegramWebhookAuthError("Invalid or missing Telegram secret token")

    @staticmethod
    def derive_actor(message: dict[str, Any]) -> str:
        """Derive an actor identifier from a Telegram ``message.from`` dict.

        Use the numeric ``id`` as the primary identifier because it is stable
        and immutable, unlike ``username`` which can be reassigned. ``username``
        is appended for human readability only (#257).
        """
        sender = cast(dict[str, Any], message.get("from", {}))
        user_id = sender.get("id")
        username = sender.get("username")
        if user_id is not None and str(user_id):
            if isinstance(username, str) and username:
                return f"telegram:{user_id}:{username}"
            return f"telegram:{user_id}"
        if isinstance(username, str) and username:
            return f"telegram:unknown:{username}"
        return "telegram-user"

    @staticmethod
    def _extract_message(update: dict[str, Any]) -> dict[str, Any]:
        """Return the message-like object from a Telegram update.

        Handles standard messages, channel posts, edited messages, and captions.
        """
        for key in ("message", "channel_post", "edited_message", "edited_channel_post"):
            value = update.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _extract_text(message: dict[str, Any]) -> str:
        """Return the text or caption from a Telegram message object."""
        return cast(str, message.get("text") or message.get("caption") or "")

    async def process_update(
        self,
        update: dict[str, Any],
        *,
        secret_token_header: str | None = None,
    ) -> dict[str, Any]:
        """Process a Telegram update and return a status dict."""
        self.authenticate_update(secret_token_header=secret_token_header)

        message = TelegramAdapter._extract_message(update)
        text = TelegramAdapter._extract_text(message)

        if not text.startswith("/approve"):
            return {"status": "ignored"}

        parts = text.split()
        if len(parts) < 2:
            return {"status": "ignored"}

        task_id = parts[1]
        approval_type = ApprovalType.EXECUTION
        if len(parts) >= 3:
            try:
                approval_type = ApprovalType(parts[2])
            except ValueError:
                approval_type = ApprovalType.EXECUTION

        payload = ApprovalRequest(
            task_id=task_id,
            approval_type=approval_type,
            source="telegram",
            actor=self.derive_actor(message),
            timestamp=datetime.now(UTC).isoformat(),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/approvals",
                json=payload.model_dump(mode="json"),
            )
            response.raise_for_status()

        return {"status": "ok"}
