"""HTTP client for Plane CE REST API.

This is the real implementation used in Phase 2 to project Controller state to
Plane CE and to read task/dependency data for runtime DAG materialization.
"""

import json
from typing import Any, cast
from urllib.parse import quote

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
        self.project_id = project_id or settings.plane_project_id or ""
        self.timeout = timeout

    def _project_id(self, project_id: str | None = None) -> str:
        """Return the effective project ID or raise if it is not configured."""
        effective = project_id or self.project_id
        if not effective:
            raise PlaneClientError(
                "Plane project_id is required but not configured"
            )
        return effective

    def _client(self) -> httpx.AsyncClient:
        """Return a configured httpx client.

        SECURITY: the ``X-API-Key`` header contains a secret. Never log
        ``exc.request`` or ``exc.request.headers`` from a caught ``httpx``
        exception, as that would leak the API token into logs/audit (#249).
        """
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers={"X-API-Key": self.api_key},
        )

    async def _request(
        self,
        method: str,
        path: str,
        project_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute an HTTP request and return JSON, wrapping all failures.

        Catches transport-level errors (timeouts, connection failures),
        HTTP-status errors, and malformed JSON and re-raises them as
        ``PlaneClientError`` so callers do not leak raw httpx exceptions.
        """
        if path.startswith("/projects/"):
            self._project_id(project_id)
        url = self._url(path)
        try:
            async with self._client() as client:
                response = await client.request(method, url, **kwargs)
                self._raise_for_status(response)
                return cast(dict[str, Any], response.json())
        except PlaneClientError:
            raise
        except httpx.HTTPError as exc:
            raise PlaneClientError(
                f"Plane request failed: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise PlaneClientError(
                f"Plane returned invalid JSON: {exc}"
            ) from exc
        except Exception as exc:
            raise PlaneClientError(
                f"Unexpected Plane request error: {exc}"
            ) from exc

    @staticmethod
    def _path_segment(segment: str) -> str:
        """URL-encode a user-supplied path segment."""
        return quote(segment, safe="")

    def _url(self, path: str) -> str:
        # Path segments are already encoded by callers; encode the workspace
        # slug as well to be defensive.
        workspace = quote(self.workspace_slug, safe="")
        return f"{self.base_url}/api/v1/workspaces/{workspace}{path}"

    async def list_projects(self) -> dict[str, Any]:
        """List projects in the workspace."""
        return await self._request("GET", "/projects/")

    async def get_project(self, project_id: str | None = None) -> dict[str, Any]:
        """Get a project by ID."""
        project_id = project_id or self.project_id
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/",
            project_id=project_id,
        )

    async def list_issues(
        self,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """List issues in a project."""
        project_id = project_id or self.project_id
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/issues/",
            project_id=project_id,
            params=params or {},
        )

    async def find_issue_by_controller_task_id(
        self,
        controller_task_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find an existing issue carrying a Controller task identifier."""
        response = await self.list_all_issues(project_id=project_id)
        issues = response.get("results", [])
        if not isinstance(issues, list):
            return None
        for issue in issues:
            if isinstance(issue, dict) and issue.get("controller_task_id") == (
                controller_task_id
            ):
                return issue
        return None

    async def get_issue(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a single issue."""
        project_id = project_id or self.project_id
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/issues/{self._path_segment(issue_id)}/",
            project_id=project_id,
        )

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

        return await self._request(
            "POST",
            f"/projects/{self._path_segment(project_id)}/issues/",
            project_id=project_id,
            json=payload,
        )

    async def update_issue(
        self,
        issue_id: str,
        fields: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Update one or more issue fields."""
        project_id = project_id or self.project_id
        return await self._request(
            "PATCH",
            f"/projects/{self._path_segment(project_id)}/issues/{self._path_segment(issue_id)}/",
            project_id=project_id,
            json=fields,
        )

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
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/issues/{self._path_segment(issue_id)}/comments/",
            project_id=project_id,
        )

    async def add_comment(
        self,
        issue_id: str,
        text: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a comment to an issue."""
        project_id = project_id or self.project_id
        return await self._request(
            "POST",
            f"/projects/{self._path_segment(project_id)}/issues/{self._path_segment(issue_id)}/comments/",
            project_id=project_id,
            json={"comment_html": text},
        )

    async def list_issue_dependencies(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List relations for an issue.

        Uses Plane CE's ``work-items/{id}/relations/`` endpoint, which returns
        a grouped dict keyed by relation type. ``blocked_by`` contains the
        issues the requested issue depends on; ``blocking`` contains issues
        that depend on it.
        """
        project_id = project_id or self.project_id
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/work-items/{self._path_segment(issue_id)}/relations/",
            project_id=project_id,
        )

    async def list_all_issues(
        self,
        project_id: str | None = None,
        per_page: int = 1000,
    ) -> dict[str, Any]:
        """List all issues in a project, following Plane pagination.

        Plane CE paginates list endpoints with ``next_page_results``. This
        method keeps fetching pages until no next page remains and returns a
        single merged response dict containing all ``results``.
        """
        project_id = project_id or self.project_id
        all_results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"per_page": per_page}
        next_cursor: str | None = None
        last_page: dict[str, Any] = {}
        max_pages = 1000

        for _ in range(max_pages):
            if next_cursor is not None:
                params["cursor"] = next_cursor
            page = await self._request(
                "GET",
                f"/projects/{self._path_segment(project_id)}/issues/",
                project_id=project_id,
                params=params,
            )
            last_page = page
            items = page.get("results")
            if isinstance(items, list):
                all_results.extend(items)
            next_cursor = page.get("next_cursor")
            has_more = page.get("next_page_results")
            if not has_more:
                break
        else:
            raise PlaneClientError(
                f"Plane pagination exceeded {max_pages} pages; aborting"
            )

        merged = dict(last_page)
        merged["results"] = all_results
        return merged

    async def list_states(
        self,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List states available in a project."""
        project_id = project_id or self.project_id
        return await self._request(
            "GET",
            f"/projects/{self._path_segment(project_id)}/states/",
            project_id=project_id,
        )

    async def list_workspace_members(self) -> dict[str, Any]:
        """List members of the workspace."""
        result = await self._request("GET", "/members/")
        if isinstance(result, list):
            return {"results": result}
        return result

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlaneClientError(
                f"Plane API error {response.status_code}: {response.text}"
            ) from exc
