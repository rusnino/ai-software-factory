"""HTTP client for macro-agent runs."""

from typing import Any, cast

import httpx

from governance_controller.config import settings


class MacroAgentClient:
    """Async HTTP client for the macro-agent runs API."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.macro_agent_base_url).rstrip("/")

    async def start(self, payload: dict[str, object]) -> dict[str, Any]:
        """Start a new macro-agent run."""
        async with self._client() as client:
            response = await client.post(f"{self.base_url}/runs", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

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
        """Return a configured httpx client with explicit timeouts."""
        headers: dict[str, str] = {}
        if settings.macro_agent_api_secret:
            headers["X-Macro-Agent-Secret"] = settings.macro_agent_api_secret
        return httpx.AsyncClient(
            timeout=settings.macro_agent_timeout_seconds,
            headers=headers,
        )
