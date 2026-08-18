"""Telegram inbound approval adapter stub.

This adapter translates Telegram update payloads into Controller approval
requests. It performs no polling/long-polling; it is intended to be invoked
from a webhook handler or future polling loop.
"""

from datetime import UTC, datetime

import httpx

from governance_controller.constants import ApprovalType
from governance_controller.schemas.approval import ApprovalRequest


class TelegramAdapter:
    """Stub adapter for inbound Telegram /approve commands."""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url

    async def process_update(self, update: dict) -> dict:
        """Process a Telegram update and return a status dict."""
        message = update.get("message", {})
        text = message.get("text", "")

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
            actor="telegram-user",
            timestamp=datetime.now(UTC).isoformat(),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/approvals",
                json=payload.model_dump(mode="json"),
            )
            response.raise_for_status()

        return {"status": "ok"}
