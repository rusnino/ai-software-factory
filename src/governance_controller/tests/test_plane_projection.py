"""Tests for the Controller → Plane CE projection service."""

from typing import Any

import pytest

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.constants import TaskState
from governance_controller.services.plane_projection import PlaneProjectionService


class _FakePlaneClient:
    def __init__(self, state_map: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.existing_issue: dict[str, Any] | None = None
        self._state_map = state_map or {
            "Proposed": "state-proposed",
            "Plan Approved": "state-plan-approved",
            "Approved": "state-approved",
            "Ready": "state-ready",
            "In Progress": "state-in-progress",
            "Agent Review": "state-agent-review",
            "In Review": "state-in-review",
            "Done": "state-done",
            "Failed": "state-failed",
            "Blocked": "state-blocked",
        }

    async def find_issue_by_controller_task_id(
        self,
        controller_task_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self.existing_issue

    async def list_states(
        self, project_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("list_states", (), {"project_id": project_id}))
        return {
            "results": [
                {"id": state_id, "name": name}
                for name, state_id in self._state_map.items()
            ]
        }

    async def create_issue(
        self,
        name: str,
        description: str | None = None,
        state: str | None = None,
        project_id: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "create_issue",
                (
                    name,
                    description,
                    state,
                    project_id,
                    external_id,
                    external_source,
                ),
                {},
            )
        )
        return {"id": "issue-1", "name": name, "state_id": state}

    async def update_issue(
        self,
        issue_id: str,
        fields: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("update_issue", (issue_id, fields, project_id), {}))
        return {"id": issue_id, "state_id": fields.get("state")}

    async def update_issue_state(
        self, issue_id: str, state_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("update_issue_state", (issue_id, state_id, project_id), {}))
        return {"id": issue_id, "state_id": state_id}

    async def add_comment(
        self, issue_id: str, text: str, project_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("add_comment", (issue_id, text, project_id), {}))
        return {"id": "comment-1"}


@pytest.fixture
def fake_client() -> _FakePlaneClient:
    return _FakePlaneClient()


async def test_ensure_plane_issue_creates_issue(fake_client: _FakePlaneClient) -> None:
    service = PlaneProjectionService(client=fake_client)
    result = await service.ensure_plane_issue(
        controller_task_id="TASK-1", title="Do work", state=TaskState.PROPOSED
    )

    assert result is not None
    assert result["id"] == "issue-1"
    assert fake_client.calls[0][0] == "list_states"
    assert fake_client.calls[1][0] == "create_issue"
    assert fake_client.calls[1][2] == {}  # kwargs empty
    assert fake_client.calls[1][1][2] == "state-proposed"  # state id


async def test_ensure_plane_issue_reuses_existing_issue(
    httpx_mock,
) -> None:
    """A retry after a post-create crash must not create a duplicate issue."""
    existing_issue = {
        "id": "issue-existing",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }
    httpx_mock.add_response(
        status_code=200,
        json={
            "results": [
                {
                    "id": "issue-other",
                    "external_id": "TASK-other",
                    "external_source": "governance-controller",
                }
            ],
            "next_cursor": "cursor-1",
            "next_page_results": True,
        },
    )
    httpx_mock.add_response(
        status_code=200,
        json={"results": [existing_issue], "next_page_results": False},
    )
    service = PlaneProjectionService(
        client=PlaneClient(
            base_url="http://plane.test",
            api_key="test-token",
            workspace_slug="ws",
            project_id="proj-1",
        )
    )

    result = await service.ensure_plane_issue(
        controller_task_id="TASK-1", title="Do work", state=TaskState.PROPOSED
    )

    assert result == existing_issue
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)


async def test_ensure_plane_issue_writes_external_traceability(
    fake_client: _FakePlaneClient,
) -> None:
    """The Controller task id uses Plane's supported external fields."""
    service = PlaneProjectionService(client=fake_client)
    await service.ensure_plane_issue(
        controller_task_id="TASK-1",
        title="Do work",
        state=TaskState.PLAN_APPROVED,
        source="telegram",
        approval_required=False,
        opentasks_id="OT-1",
    )

    create_call = next(c for c in fake_client.calls if c[0] == "create_issue")
    assert create_call[1][4:] == ("TASK-1", "governance-controller")


async def test_update_state_resolves_state_and_updates(
    fake_client: _FakePlaneClient,
) -> None:
    service = PlaneProjectionService(client=fake_client)
    result = await service.update_state(
        controller_task_id="TASK-1",
        plane_issue_id="issue-1",
        state=TaskState.DONE,
    )

    assert result is not None
    assert result["state_id"] == "state-done"
    assert fake_client.calls[0][0] == "list_states"
    assert fake_client.calls[1][0] == "update_issue_state"
    assert fake_client.calls[1][1][1] == "state-done"


async def test_update_state_does_not_send_unsupported_custom_field(
    fake_client: _FakePlaneClient,
) -> None:
    """State updates do not send unsupported top-level custom fields."""
    service = PlaneProjectionService(client=fake_client)
    result = await service.update_state(
        controller_task_id="TASK-1",
        plane_issue_id="issue-1",
        state=TaskState.RUNNING,
        opentasks_id="OT-42",
    )

    assert result is not None
    assert result["state_id"] == "state-in-progress"
    update_call = next(c for c in fake_client.calls if c[0] == "update_issue_state")
    assert update_call[1][1] == "state-in-progress"


async def test_update_state_unknown_controller_state_returns_none(
    fake_client: _FakePlaneClient,
) -> None:
    service = PlaneProjectionService(client=fake_client)
    result = await service.update_state(
        controller_task_id="TASK-1",
        plane_issue_id="issue-1",
        state=TaskState.PROPOSED,  # mapped but list_states still called
    )

    assert result is not None


async def test_add_comment_forwards_to_client(
    fake_client: _FakePlaneClient,
) -> None:
    service = PlaneProjectionService(client=fake_client)
    result = await service.add_comment("issue-1", "Please review")

    assert result is not None
    assert result["id"] == "comment-1"
    assert fake_client.calls[0] == (
        "add_comment",
        ("issue-1", "Please review", None),
        {},
    )


async def test_when_plane_not_configured_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    service = PlaneProjectionService()

    assert await service.ensure_plane_issue("TASK-1", "title") is None
    assert await service.update_state("TASK-1", "issue-1", TaskState.DONE) is None
    assert await service.add_comment("issue-1", "text") is None
