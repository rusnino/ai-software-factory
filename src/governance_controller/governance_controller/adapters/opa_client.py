"""Open Policy Agent (OPA) client for delegated policy evaluation."""

import json
from typing import Any, cast

import httpx

from governance_controller.config import settings


class OPAClientError(Exception):
    """Raised when an OPA call fails or returns an unexpected result."""


class OPAClient:
    """Async HTTP client for OPA's Data API.

    Args:
        base_url: OPA base URL. Defaults to ``settings.opa_base_url``.
        policy_path: OPA document path. Defaults to
            ``settings.opa_policy_path``.
        timeout: Request timeout in seconds. Defaults to
            ``settings.opa_timeout_seconds``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        policy_path: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.opa_base_url).rstrip("/")
        self.policy_path = policy_path or settings.opa_policy_path
        self.timeout = timeout or settings.opa_timeout_seconds

    def _client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if settings.opa_api_token:
            headers["Authorization"] = f"Bearer {settings.opa_api_token}"
        return httpx.AsyncClient(timeout=self.timeout, headers=headers)

    async def evaluate(
        self,
        input_data: dict[str, object],
    ) -> dict[str, Any]:
        """Evaluate policy against ``input_data``.

        Returns the OPA result document. The caller is responsible for
        interpreting ``result.allow`` and ``result.violations``.
        """
        if not self.base_url:
            raise OPAClientError("OPA base URL is not configured")

        url = f"{self.base_url}/v1/data/{self.policy_path}"
        try:
            async with self._client() as client:
                response = await client.post(url, json={"input": input_data})
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise OPAClientError(
                        f"OPA returned {response.status_code}: {response.text}"
                    ) from exc

                try:
                    body = cast(dict[str, Any], response.json())
                except json.JSONDecodeError as exc:
                    raise OPAClientError(
                        f"OPA returned non-JSON response: {response.text[:200]}"
                    ) from exc
                result = body.get("result")
                if not isinstance(result, dict):
                    raise OPAClientError(
                        f"OPA result missing or malformed: {body}"
                    )
                return result
        except httpx.HTTPError as exc:
            raise OPAClientError(f"OPA request failed: {exc}") from exc
