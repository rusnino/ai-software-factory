"""HTTP client for Plane CE REST API.

This is the real implementation used in Phase 2 to project Controller state to
Plane CE and to read task/dependency data for runtime DAG materialization.
"""

from typing import Any, cast

import httpx

from governance_controller.config import settings


class PlaneClientError(Exception):
    """Raised when a Plane CE API call fails."""


class PlaneClient:
    """Async HTTP client for Plane CE.

    Args:
        base_url: Plane CE base URL. Defaults to ``settings.plane_base_url``.
        api_key: Plane API key. Defaults to ``settings.plane_api_token``.
        workspace_slug: Plane workspace slug. Defaults to
            ``settings.plane_workspace_slug``.
        project_id: Plane project UUID. Defaults to
            ``settings.plane_project_id``.
        timeout: Request timeout in seconds. Defaults to 30.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        workspace_slug: str | None = None,
        project_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.plane_base_url).rstrip("/")
        self.api_key = api_key or settings.plane_api_token
        self.workspace_slug = workspace_slug or settings.plane_workspace_slug
        self.project_id = project_id or settings.plane_project_id
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers={"X-API-Key": self.api_key},
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/workspaces/{self.workspace_slug}{path}"

    async def list_projects(self) -> dict[str, Any]:
        """List projects in the workspace."""
        async with self._client() as client:
            response = await client.get(self._url("/projects/"))
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Get a project by ID."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(self._url(f"/projects/{project_id}/"))
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def list_issues(
        self,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """List issues in a project."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(
                self._url(f"/projects/{project_id}/issues/"),
                params=params or {},
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def get_issue(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a single issue."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(
                self._url(f"/projects/{project_id}/issues/{issue_id}/")
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def create_issue(
        self,
        name: str,
        description: str | None = None,
        state: str | None = None,
        project_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue.

        Args:
            name: Issue title.
            description: Optional HTML/markdown description.
            state: Optional Plane state UUID.
            project_id: Optional project override.
            extra: Additional Plane fields.
        """
        project_id = project_id or self.project_id
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description_html"] = description
        if state is not None:
            payload["state"] = state
        if extra is not None:
            payload.update(extra)

        async with self._client() as client:
            response = await client.post(
                self._url(f"/projects/{project_id}/issues/"),
                json=payload,
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def update_issue(
        self,
        issue_id: str,
        fields: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Update one or more issue fields."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.patch(
                self._url(f"/projects/{project_id}/issues/{issue_id}/"),
                json=fields,
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def update_issue_state(
        self,
        issue_id: str,
        state_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Update the state of an issue."""
        return await self.update_issue(
            issue_id, {"state": state_id}, project_id=project_id
        )

    async def list_comments(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List comments on an issue."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(
                self._url(f"/projects/{project_id}/issues/{issue_id}/comments/")
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def add_comment(
        self,
        issue_id: str,
        text: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a comment to an issue."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.post(
                self._url(f"/projects/{project_id}/issues/{issue_id}/comments/"),
                json={"comment_html": text},
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def list_issue_dependencies(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List dependencies for an issue."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(
                self._url(f"/projects/{project_id}/issues/{issue_id}/dependencies/")
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def list_all_issues(
        self,
        project_id: str | None = None,
        page_size: int = 1000,
    ) -> dict[str, Any]:
        """List all issues in a project, following Plane pagination cursors.

        Plane CE paginates list endpoints with ``next_cursor``. This method
        keeps fetching pages until no next cursor remains and returns a single
        merged response dict containing all ``results``.
        """
        project_id = project_id or self.project_id
        all_results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"page_size": page_size}
        next_cursor: str | None = None
        last_page: dict[str, Any] = {}

        async with self._client() as client:
            while True:
                if next_cursor is not None:
                    params["cursor"] = next_cursor
                response = await client.get(
                    self._url(f"/projects/{project_id}/issues/"),
                    params=params,
                )
                self._raise_for_status(response)
                page = cast(dict[str, Any], response.json())
                last_page = page
                items = page.get("results")
                if isinstance(items, list):
                    all_results.extend(items)
                next_cursor = page.get("next_cursor")
                if not next_cursor:
                    break

        merged = dict(last_page)
        merged["results"] = all_results
        return merged

    async def list_states(
        self,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List states available in a project."""
        project_id = project_id or self.project_id
        async with self._client() as client:
            response = await client.get(self._url(f"/projects/{project_id}/states/"))
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    async def list_workspace_members(self) -> dict[str, Any]:
        """List members of the workspace."""
        async with self._client() as client:
            response = await client.get(
                self._url("/members/"),
            )
            self._raise_for_status(response)
            return cast(dict[str, Any], response.json())

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlaneClientError(
                f"Plane API error {response.status_code}: {response.text}"
            ) from exc
