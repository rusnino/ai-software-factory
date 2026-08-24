"""Tests for the Plane CE HTTP client."""


import pytest

from governance_controller.adapters.plane_client import PlaneClient, PlaneClientError
from governance_controller.config import Settings


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Return a Settings instance with Plane config populated."""
    settings = Settings(
        plane_base_url="http://plane.test",
        plane_api_token="test-token",
        plane_workspace_slug="ws",
        plane_project_id="proj-1",
    )
    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.settings", settings
    )
    return settings


async def test_client_uses_config_defaults(settings_override: Settings) -> None:
    """The client reads config from Settings when no args are passed."""
    client = PlaneClient()
    assert client.base_url == "http://plane.test"
    assert client.api_key == "test-token"
    assert client.workspace_slug == "ws"
    assert client.project_id == "proj-1"


async def test_client_accepts_explicit_args() -> None:
    """Explicit constructor args override Settings."""
    client = PlaneClient(
        base_url="http://explicit.test",
        api_key="explicit-token",
        workspace_slug="explicit-ws",
        project_id="explicit-proj",
    )
    assert client.base_url == "http://explicit.test"
    assert client.api_key == "explicit-token"
    assert client.workspace_slug == "explicit-ws"
    assert client.project_id == "explicit-proj"


async def test_list_projects_request(httpx_mock, settings_override: Settings) -> None:
    """list_projects calls the correct endpoint."""
    httpx_mock.add_response(
        status_code=200,
        json={"results": [{"id": "proj-1", "name": "Controller"}]},
    )
    client = PlaneClient()
    result = await client.list_projects()

    assert result["results"][0]["id"] == "proj-1"
    request = httpx_mock.get_request()
    assert request.url == "http://plane.test/api/v1/workspaces/ws/projects/"
    assert request.headers["X-API-Key"] == "test-token"


async def test_list_issues_request(httpx_mock, settings_override: Settings) -> None:
    """list_issues calls the correct endpoint."""
    httpx_mock.add_response(status_code=200, json={"results": []})
    client = PlaneClient()
    result = await client.list_issues()

    assert result["results"] == []
    request = httpx_mock.get_request()
    assert (
        str(request.url)
        == "http://plane.test/api/v1/workspaces/ws/projects/proj-1/issues/"
    )


async def test_create_issue_request(httpx_mock, settings_override: Settings) -> None:
    """create_issue serializes the payload correctly."""
    httpx_mock.add_response(
        status_code=201,
        json={"id": "issue-1", "name": "Hello Plane"},
    )
    client = PlaneClient()
    result = await client.create_issue(
        name="Hello Plane",
        description="<p>body</p>",
        state="state-1",
    )

    assert result["id"] == "issue-1"
    request = httpx_mock.get_request()
    import json

    body = json.loads(request.content)
    assert body["name"] == "Hello Plane"
    assert body["description_html"] == "<p>body</p>"
    assert body["state"] == "state-1"


async def test_update_issue_state_request(
    httpx_mock, settings_override: Settings
) -> None:
    """update_issue_state sends a PATCH with the state UUID."""
    httpx_mock.add_response(status_code=200, json={"id": "issue-1"})
    client = PlaneClient()
    result = await client.update_issue_state("issue-1", "state-2")

    assert result["id"] == "issue-1"
    request = httpx_mock.get_request()
    assert request.method == "PATCH"
    import json

    assert json.loads(request.content) == {"state": "state-2"}


async def test_add_comment_request(httpx_mock, settings_override: Settings) -> None:
    """add_comment posts a comment_html payload."""
    httpx_mock.add_response(status_code=201, json={"id": "comment-1"})
    client = PlaneClient()
    result = await client.add_comment("issue-1", "verification failed")

    assert result["id"] == "comment-1"
    request = httpx_mock.get_request()
    import json

    body = json.loads(request.content)
    assert body["comment_html"] == "verification failed"


async def test_list_issue_dependencies_request(
    httpx_mock, settings_override: Settings
) -> None:
    """list_issue_dependencies calls the dependencies endpoint."""
    httpx_mock.add_response(status_code=200, json={"results": []})
    client = PlaneClient()
    result = await client.list_issue_dependencies("issue-1")

    assert result["results"] == []
    request = httpx_mock.get_request()
    assert (
        str(request.url)
        == "http://plane.test/api/v1/workspaces/ws/projects/proj-1/issues/issue-1/dependencies/"
    )


async def test_api_error_raises_plane_client_error(
    httpx_mock, settings_override: Settings
) -> None:
    """HTTP errors are wrapped in PlaneClientError."""
    httpx_mock.add_response(status_code=404, text='{"detail":"not found"}')
    client = PlaneClient()

    with pytest.raises(PlaneClientError):
        await client.get_issue("missing")


async def test_update_issue_accepts_extra_fields(
    httpx_mock, settings_override: Settings
) -> None:
    """update_issue passes through arbitrary fields for custom Plane fields."""
    httpx_mock.add_response(status_code=200, json={"id": "issue-1"})
    client = PlaneClient()
    await client.update_issue(
        "issue-1",
        {
            "controller_task_id": "TASK-1",
            "opentasks_id": "OT-1",
        },
    )
    import json

    body = json.loads(httpx_mock.get_request().content)
    assert body["controller_task_id"] == "TASK-1"
    assert body["opentasks_id"] == "OT-1"
