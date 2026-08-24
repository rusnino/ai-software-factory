"""Contract tests against the real local Plane CE instance.

These tests require:
  - Plane CE running at GC_PLANE_BASE_URL (default http://127.0.0.1:8081)
  - GC_PLANE_API_TOKEN, GC_PLANE_WORKSPACE_SLUG, and GC_PLANE_PROJECT_ID set.

They are skipped if the client cannot connect or if config is missing.
"""

import os
from uuid import uuid4

import pytest

from governance_controller.adapters.plane_client import PlaneClient


@pytest.fixture
def real_client() -> PlaneClient:
    """Return a PlaneClient configured from environment."""
    base_url = os.environ.get("GC_PLANE_BASE_URL", "http://127.0.0.1:8081")
    api_token = os.environ.get("GC_PLANE_API_TOKEN", "")
    workspace_slug = os.environ.get("GC_PLANE_WORKSPACE_SLUG", "")
    project_id = os.environ.get("GC_PLANE_PROJECT_ID", "")

    missing = [
        k
        for k, v in {
            "GC_PLANE_API_TOKEN": api_token,
            "GC_PLANE_WORKSPACE_SLUG": workspace_slug,
            "GC_PLANE_PROJECT_ID": project_id,
        }.items()
        if not v
    ]
    if missing:
        pytest.skip(f"missing Plane config: {', '.join(missing)}")

    return PlaneClient(
        base_url=base_url,
        api_key=api_token,
        workspace_slug=workspace_slug,
        project_id=project_id,
    )


@pytest.mark.asyncio
async def test_list_projects(real_client: PlaneClient) -> None:
    """Can list projects in the configured workspace."""
    result = await real_client.list_projects()
    assert "results" in result
    assert any(p["id"] == real_client.project_id for p in result["results"])


@pytest.mark.asyncio
async def test_create_and_get_issue(real_client: PlaneClient) -> None:
    """Can create an issue and read it back."""
    unique = uuid4().hex[:8]
    created = await real_client.create_issue(
        name=f"Contract test issue {unique}",
        description="<p>created by contract test</p>",
    )

    assert "id" in created
    issue_id = created["id"]

    fetched = await real_client.get_issue(issue_id)
    assert fetched["id"] == issue_id
    assert fetched["name"] == f"Contract test issue {unique}"

    # Cleanup
    await real_client.update_issue(issue_id, {"is_draft": True})
