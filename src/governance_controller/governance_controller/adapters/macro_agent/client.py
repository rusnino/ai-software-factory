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
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/runs", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def status(self, run_id: str) -> dict[str, Any]:
        """Query macro-agent run status."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/runs/{run_id}")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancel a macro-agent run."""
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/runs/{run_id}/cancel")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def collect(self, run_id: str) -> dict[str, Any]:
        """Collect macro-agent run results."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/runs/{run_id}/collect")
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
