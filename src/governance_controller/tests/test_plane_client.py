"""Tests for the Plane CE HTTP client."""

import httpx
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


async def test_find_issue_by_controller_task_id_uses_server_filter(
    httpx_mock, settings_override: Settings
) -> None:
    """Lookup asks Plane for one external traceability match."""
    matching_issue = {
        "id": "issue-1",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }
    httpx_mock.add_response(
        status_code=200,
        json=matching_issue,
    )

    result = await PlaneClient().find_issue_by_controller_task_id("TASK-1")

    assert result == matching_issue
    request = httpx_mock.get_request()
    assert request is not None
    assert request.method == "GET"
    assert request.url.params["external_id"] == "TASK-1"
    assert request.url.params["external_source"] == "governance-controller"
    assert "per_page" not in request.url.params


async def test_find_issue_by_controller_task_id_returns_none_for_plane_404(
    httpx_mock, settings_override: Settings
) -> None:
    """A filtered Plane lookup treats a missing external match as absent."""
    httpx_mock.add_response(status_code=404, json={"detail": "Not found"})

    result = await PlaneClient().find_issue_by_controller_task_id("TASK-1")

    assert result is None


async def test_find_issue_by_controller_task_id_propagates_non_404_plane_errors(
    httpx_mock, settings_override: Settings
) -> None:
    """A non-404 Plane error must propagate as PlaneClientError, not be swallowed."""
    httpx_mock.add_response(status_code=500, json={"detail": "Internal Server Error"})

    with pytest.raises(PlaneClientError) as exc_info:
        await PlaneClient().find_issue_by_controller_task_id("TASK-1")

    assert exc_info.value.status_code == 500


async def test_find_issue_by_controller_task_id_uses_external_id(
    httpx_mock, settings_override: Settings
) -> None:
    """Lookup matches Plane's persisted external traceability fields."""
    matching_issue = {
        "id": "issue-1",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [
                {
                    "id": "issue-wrong-source",
                    "external_id": "TASK-1",
                    "external_source": "other-system",
                },
                matching_issue,
            ]
        },
    )

    result = await PlaneClient().find_issue_by_controller_task_id("TASK-1")

    assert result == matching_issue


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


async def test_create_issue_includes_external_traceability(
    httpx_mock, settings_override: Settings
) -> None:
    """create_issue serializes Plane's supported external fields."""
    httpx_mock.add_response(
        status_code=201,
        json={
            "id": "issue-1",
            "external_id": "TASK-1",
            "external_source": "governance-controller",
        },
    )
    client = PlaneClient()

    await client.create_issue(
        name="Hello Plane",
        external_id="TASK-1",
        external_source="governance-controller",
    )

    import json

    body = json.loads(httpx_mock.get_request().content)
    assert body["external_id"] == "TASK-1"
    assert body["external_source"] == "governance-controller"


async def test_upsert_work_item_property_value_request(
    httpx_mock, settings_override: Settings
) -> None:
    """Property values use Plane's documented work-item endpoint."""
    httpx_mock.add_response(status_code=200, json={"id": "property-value-1"})
    client = PlaneClient()

    result = await client.upsert_work_item_property_value(
        issue_id="issue-1",
        property_id="property-1",
        value=False,
    )

    assert result["id"] == "property-value-1"
    request = httpx_mock.get_request()
    assert (
        str(request.url)
        == "http://plane.test/api/v1/workspaces/ws/projects/proj-1/"
        "work-items/issue-1/work-item-properties/property-1/values/"
    )
    import json

    assert json.loads(request.content) == {"value": False}


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
    """list_issue_dependencies calls the real work-items relations endpoint."""
    httpx_mock.add_response(status_code=200, json={"blocked_by": [], "blocking": []})
    client = PlaneClient()
    result = await client.list_issue_dependencies("issue-1")

    assert result["blocked_by"] == []
    request = httpx_mock.get_request()
    assert (
        str(request.url)
        == "http://plane.test/api/v1/workspaces/ws/projects/proj-1/work-items/issue-1/relations/"
    )


async def test_api_error_raises_plane_client_error(
    httpx_mock, settings_override: Settings
) -> None:
    """HTTP errors are wrapped in PlaneClientError."""
    httpx_mock.add_response(status_code=404, text='{"detail":"not found"}')
    client = PlaneClient()

    with pytest.raises(PlaneClientError):
        await client.get_issue("missing")


async def test_network_error_raises_plane_client_error(
    httpx_mock, settings_override: Settings
) -> None:
    """Transport errors are wrapped in PlaneClientError."""
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    client = PlaneClient()

    with pytest.raises(PlaneClientError, match="Plane request failed"):
        await client.list_projects()


async def test_malformed_json_raises_plane_client_error(
    httpx_mock, settings_override: Settings
) -> None:
    """Malformed JSON responses are wrapped in PlaneClientError."""
    httpx_mock.add_response(text="not json")
    client = PlaneClient()

    with pytest.raises(PlaneClientError, match="invalid JSON"):
        await client.list_projects()


async def test_list_all_issues_uses_next_page_results_for_termination(
    httpx_mock, settings_override: Settings
) -> None:
    """#189: pagination stops when next_page_results is false, not next_cursor."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [{"id": "issue-1"}],
            "next_cursor": "1000:1:0",
            "next_page_results": True,
        },
    )
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [{"id": "issue-2"}],
            "next_cursor": "1000:2:0",
            "next_page_results": False,
        },
    )

    client = PlaneClient()
    result = await client.list_all_issues(per_page=1000)

    assert [i["id"] for i in result["results"]] == ["issue-1", "issue-2"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert "cursor=1000%3A1%3A0" in str(requests[1].url)


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
async def test_list_all_issues_rejects_missing_next_cursor(
    httpx_mock, settings_override: Settings
) -> None:
    """A response advertising another page must include a cursor."""
    httpx_mock.add_response(
        status_code=200,
        json={"results": [], "next_page_results": True},
    )

    with pytest.raises(PlaneClientError, match="non-empty cursor"):
        await PlaneClient().list_all_issues()

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
async def test_list_all_issues_rejects_repeated_next_cursor(
    httpx_mock, settings_override: Settings
) -> None:
    """A repeated cursor must not cause another page request."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [],
            "next_cursor": "cursor-1",
            "next_page_results": True,
        },
    )
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [],
            "next_cursor": "cursor-1",
            "next_page_results": True,
        },
    )

    with pytest.raises(PlaneClientError, match="repeated cursor"):
        await PlaneClient().list_all_issues()

    assert len(httpx_mock.get_requests()) == 2


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
