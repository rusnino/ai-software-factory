"""HTTP client for macro-agent runs."""

from typing import Any, cast

import httpx

from governance_controller.config import settings
from governance_controller.schemas.macro_agent import MacroAgentStartResponse


class MacroAgentResponseError(ValueError):
    """Raised when a macro-agent response violates the wire contract."""


def classify_macro_agent_start_exception(exc: Exception) -> str:
    """Classify a macro-agent start failure by orphan risk.

    Returns one of:
    - ``never_sent``: the request never left the Controller (connect failure).
    - ``orphan_suspected``: the request was sent but the response was lost
      before the run ID could be recorded (timeout/pool exhaustion).
    - ``accepted_response_invalid``: a 2xx response was received but could not
      be parsed, so a run was likely accepted without a usable run ID.
    - ``rejected``: the macro-agent returned an HTTP error status.
    - ``unknown``: any other failure.
    """
    if isinstance(exc, MacroAgentResponseError):
        return "accepted_response_invalid"
    if isinstance(exc, httpx.HTTPStatusError):
        return "rejected"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "never_sent"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "orphan_suspected"
    return "unknown"


class MacroAgentClient:
    """Async HTTP client for the macro-agent runs API."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.macro_agent_base_url).rstrip("/")

    async def start(self, payload: dict[str, object]) -> dict[str, Any]:
        """Start a new macro-agent run."""
        async with self._client() as client:
            response = await client.post(f"{self.base_url}/runs", json=payload)
            response.raise_for_status()
            try:
                parsed = MacroAgentStartResponse.model_validate(response.json())
            except (TypeError, ValueError) as exc:
                raise MacroAgentResponseError(
                    "Invalid macro-agent start response"
                ) from exc
            return parsed.model_dump(exclude_none=True)

    async def status(self, run_id: str) -> dict[str, Any]:
        """Query macro-agent run status."""
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/runs/{run_id}")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancel a macro-agent run."""
        async with self._client() as client:
            response = await client.post(f"{self.base_url}/runs/{run_id}/cancel")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def collect(self, run_id: str) -> dict[str, Any]:
        """Collect macro-agent run results."""
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/runs/{run_id}/collect")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def feedback(self, run_id: str, payload: dict[str, object]) -> dict[str, Any]:
        """Push failure feedback to a macro-agent run."""
        async with self._client() as client:
            response = await client.post(
                f"{self.base_url}/runs/{run_id}/feedback", json=payload
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    def _client(self) -> httpx.AsyncClient:
        """Return a configured httpx client with explicit timeouts.

        SECURITY: this header contains a secret. Never log ``exc.request`` or
        ``exc.request.headers`` from a caught ``httpx`` exception, as that
        would leak the secret into logs/audit (#249).
        """
        headers: dict[str, str] = {}
        if settings.macro_agent_api_secret:
            headers["X-Macro-Agent-Secret"] = settings.macro_agent_api_secret
        return httpx.AsyncClient(
            timeout=settings.macro_agent_timeout_seconds,
            headers=headers,
        )
