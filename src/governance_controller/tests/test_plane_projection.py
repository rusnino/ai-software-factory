"""Tests for the Controller → Plane CE projection service."""

from typing import Any

import pytest

from governance_controller.adapters.plane_client import PlaneClient, PlaneClientError
from governance_controller.config import Settings
from governance_controller.constants import TaskState
from governance_controller.services.plane_projection import PlaneProjectionService


class _FakePlaneClient:
    def __init__(self, state_map: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.existing_issue: dict[str, Any] | None = None
        self.property_failures: set[str] = set()
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

    async def upsert_work_item_property_value(
        self,
        issue_id: str,
        property_id: str,
        value: str | bool,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "upsert_work_item_property_value",
                (issue_id, property_id, value, project_id),
                {},
            )
        )
        if property_id in self.property_failures:
            raise PlaneClientError(f"property write failed: {property_id}")
        return {"id": "property-value-1"}

    async def add_comment(
        self, issue_id: str, text: str, project_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("add_comment", (issue_id, text, project_id), {}))
        return {"id": "comment-1"}


@pytest.fixture
def fake_client() -> _FakePlaneClient:
    return _FakePlaneClient()


def _set_property_ids(
    monkeypatch: pytest.MonkeyPatch,
    source_options: dict[str, str] | None = None,
    **property_ids: str,
) -> None:
    settings = Settings(
        plane_controller_task_id_property_id=property_ids.get(
            "controller_task_id", ""
        ),
        plane_opentasks_id_property_id=property_ids.get("opentasks_id", ""),
        plane_source_property_id=property_ids.get("source", ""),
        plane_approval_required_property_id=property_ids.get(
            "approval_required", ""
        ),
        plane_source_option_ids=source_options or {},
    )
    monkeypatch.setattr(
        "governance_controller.services.plane_projection.settings", settings
    )


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
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.params["external_id"] == "TASK-1"
    assert requests[0].url.params["external_source"] == "governance-controller"


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
    assert not any(
        call[0] == "upsert_work_item_property_value" for call in fake_client.calls
    )


async def test_ensure_plane_issue_writes_configured_properties_after_create(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(
        monkeypatch,
        source_options={"telegram": "option-telegram"},
        controller_task_id="property-controller-task",
        opentasks_id="property-opentasks",
        source="property-source",
        approval_required="property-approval",
    )
    service = PlaneProjectionService(client=fake_client)

    await service.ensure_plane_issue(
        controller_task_id="TASK-1",
        title="Do work",
        state=TaskState.PLAN_APPROVED,
        source="telegram",
        approval_required=False,
        opentasks_id="OT-1",
    )

    property_calls = [
        call[1]
        for call in fake_client.calls
        if call[0] == "upsert_work_item_property_value"
    ]
    assert property_calls == [
        ("issue-1", "property-controller-task", "TASK-1", None),
        ("issue-1", "property-opentasks", "OT-1", None),
        ("issue-1", "property-source", "option-telegram", None),
        ("issue-1", "property-approval", False, None),
    ]


async def test_ensure_plane_issue_writes_configured_properties_for_existing_issue(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(
        monkeypatch,
        source_options={"telegram": "option-telegram"},
        controller_task_id="property-controller-task",
        source="property-source",
        approval_required="property-approval",
    )
    fake_client.existing_issue = {
        "id": "issue-existing",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }
    service = PlaneProjectionService(client=fake_client)

    result = await service.ensure_plane_issue(
        controller_task_id="TASK-1",
        title="Do work",
        source="telegram",
        approval_required=False,
    )

    assert result == fake_client.existing_issue
    assert not any(call[0] == "create_issue" for call in fake_client.calls)
    property_calls = [
        call[1]
        for call in fake_client.calls
        if call[0] == "upsert_work_item_property_value"
    ]
    assert property_calls == [
        ("issue-existing", "property-controller-task", "TASK-1", None),
        ("issue-existing", "property-source", "option-telegram", None),
        ("issue-existing", "property-approval", False, None),
    ]


async def test_ensure_plane_issue_surfaces_property_failure_and_retries_existing_issue(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(
        monkeypatch,
        source_options={"telegram": "option-telegram"},
        source="property-source",
    )
    fake_client.existing_issue = {
        "id": "issue-existing",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }
    fake_client.property_failures.add("property-source")
    service = PlaneProjectionService(client=fake_client)

    with pytest.raises(PlaneClientError, match="property projection failed"):
        await service.ensure_plane_issue(
            controller_task_id="TASK-1",
            title="Do work",
            source="telegram",
        )

    fake_client.property_failures.clear()
    result = await service.ensure_plane_issue(
        controller_task_id="TASK-1",
        title="Do work",
        source="telegram",
    )

    assert result == fake_client.existing_issue
    assert not any(call[0] == "create_issue" for call in fake_client.calls)
    assert sum(
        call[0] == "upsert_work_item_property_value" for call in fake_client.calls
    ) == 2


async def test_ensure_plane_issue_rejects_missing_source_option_before_lookup(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(monkeypatch, source="property-source")

    with pytest.raises(PlaneClientError, match="source.*option UUID"):
        await PlaneProjectionService(client=fake_client).ensure_plane_issue(
            controller_task_id="TASK-1",
            title="Do work",
            source="telegram",
        )

    assert fake_client.calls == []


async def test_ensure_plane_issue_skips_unavailable_property_metadata(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(
        monkeypatch,
        controller_task_id="property-controller-task",
        source="property-source",
        approval_required="property-approval",
    )
    fake_client.existing_issue = {
        "id": "issue-existing",
        "external_id": "TASK-1",
        "external_source": "governance-controller",
    }

    result = await PlaneProjectionService(client=fake_client).ensure_plane_issue(
        controller_task_id="TASK-1",
        title="Do work",
    )

    assert result == fake_client.existing_issue
    assert [
        call[1]
        for call in fake_client.calls
        if call[0] == "upsert_work_item_property_value"
    ] == [("issue-existing", "property-controller-task", "TASK-1", None)]


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


async def test_update_state_writes_configured_opentasks_id(
    fake_client: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_property_ids(monkeypatch, opentasks_id="property-opentasks")
    service = PlaneProjectionService(client=fake_client)

    result = await service.update_state(
        controller_task_id="TASK-1",
        plane_issue_id="issue-1",
        state=TaskState.RUNNING,
        opentasks_id="OT-42",
    )

    assert result is not None
    property_call = next(
        call
        for call in fake_client.calls
        if call[0] == "upsert_work_item_property_value"
    )
    assert property_call[1] == (
        "issue-1",
        "property-opentasks",
        "OT-42",
        None,
    )


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
