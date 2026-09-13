"""Tests for the Plane CE webhook receiver."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.api import webhooks as webhooks_module
from governance_controller.constants import TaskState
from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.models.task import Task
from governance_controller.schemas import ProjectProfile, RepositoryConfig
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract

# Captured at import time, before any test's `_auth_ok` fixture monkeypatches
# `webhooks_module._resolve_actor_email` to a fake resolver. Tests targeting
# `_resolve_actor_email` itself must restore this real implementation, or
# they exercise the fixture's fake resolver instead of the code under test.
_REAL_RESOLVE_ACTOR_EMAIL = webhooks_module._resolve_actor_email

_STATE_UUIDS = {
    "Proposed": "state-uuid-proposed",
    "Plan Approved": "state-uuid-plan-approved",
    "Approved": "state-uuid-approved",
    "Ready": "state-uuid-ready",
    "In Progress": "state-uuid-in-progress",
    "Agent Review": "state-uuid-agent-review",
    "In Review": "state-uuid-in-review",
    "Done": "state-uuid-done",
    "Blocked": "state-uuid-blocked",
    "Failed": "state-uuid-failed",
}


def _event(
    issue_id: str = "TASK-1",
    previous_state: str = "Proposed",
    current_state: str = "Plan Approved",
    actor: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a real Plane CE webhook payload."""
    if actor is None:
        actor = {"id": "user-uuid-1", "email": "human@example.com"}
    return {
        "event": "issue",
        "action": "update",
        "webhook_id": "wh-123",
        "workspace_id": "ws-uuid-1",
        "workspace_slug": "ai-factory",
        "data": {
            "id": issue_id,
            "project_id": "proj-1",
            "name": "Some issue",
            "state": _STATE_UUIDS[current_state],
        },
        "activity": {
            "field": "state",
            "old_value": _STATE_UUIDS[previous_state],
            "new_value": _STATE_UUIDS[current_state],
            "actor": actor,
        },
    }


@pytest.fixture
def _auth_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    # Webhook endpoint is closed by default; tests that need an authenticated
    # request must use this fixture (or equivalent monkeypatching).
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_base_url",
        "http://plane.example.com",
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret",
        "secret",
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_allowed_actors",
        "human@example.com",
    )
    # Without real Plane connectivity, resolve allowed actors by direct match.
    async def _resolve(
        client: Any, actor_display_name: str
    ) -> str | None:
        allowed = {"human@example.com"}
        candidate = actor_display_name.lower()
        if candidate in allowed:
            return candidate
        return None

    monkeypatch.setattr(
        "governance_controller.api.webhooks._resolve_actor_email",
        _resolve,
    )

    async def _members(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": []}

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.list_workspace_members",
        _members,
    )

    # Map state UUIDs back to display names like Plane would.
    async def _list_states(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "results": [
                {"id": uuid, "name": name}
                for name, uuid in _STATE_UUIDS.items()
            ]
        }

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.list_states",
        _list_states,
    )


@pytest_asyncio.fixture
async def seeded_db(
    isolated_db: tuple, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncSession]:
    """Yield a session backed by a file-based isolated DB with seeded data."""
    from governance_controller.db import get_db
    from governance_controller.main import app

    engine, session_local = isolated_db

    # Patch production db globals so any code that bypasses dependency
    # overrides (e.g. PlaneClient constructing its own session) still hits the
    # isolated test database.
    import governance_controller.db as db_module

    original_engine = db_module.engine
    original_session_local = db_module.AsyncSessionLocal
    original_database_url = db_module.settings.database_url
    test_db_url = engine.url.render_as_string(hide_password=False)
    db_module.engine = engine
    db_module.AsyncSessionLocal = session_local
    db_module.settings.database_url = test_db_url

    async with session_local() as session:
        profile = ProjectProfile(
            project_id="proj-1",
            project_name="Controller",
            repository=RepositoryConfig(path="https://example.com/repo"),
            execution={"timeout_minutes": 60},
            security={"forbidden_paths": []},
            git={"signed_commits": "optional"},
        )
        session.add(
            ProjectProfileModel(
                project_id=profile.project_id,
                profile_json=profile.model_dump(mode="json"),
            )
        )

        contract = TaskContract(
            task_id="TASK-1",
            project_id="proj-1",
            proposed_by="agent-1",
            objective="Implement a thing",
            acceptance=["It works"],
            execution=ExecutionConfig(max_retries=2),
        )
        task = Task(
            id="TASK-1",
            project_id="proj-1",
            state=TaskState.PROPOSED,
            proposed_by="agent-1",
            task_contract_json=contract.model_dump(mode="json"),
        )
        session.add(task)
        await session.commit()

        async def _override_get_db() -> AsyncGenerator[AsyncSession]:
            yield session

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
            db_module.engine = original_engine
            db_module.AsyncSessionLocal = original_session_local
            db_module.settings.database_url = original_database_url


@pytest_asyncio.fixture
async def async_client(seeded_db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Return an HTTP client pointed at the FastAPI app."""
    from governance_controller.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_webhook_rejects_unconfigured_secret(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webhook endpoint fails closed when no secret is configured."""
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", ""
    )
    response = await async_client.post("/webhooks/plane", json=_event())
    assert response.status_code == 401


async def test_webhook_rejects_empty_allowed_actors(
    async_client: AsyncClient,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured secret with no allowed actors list is still closed."""
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", "secret"
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_allowed_actors", ""
    )
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 403


async def test_webhook_ignores_non_state_event(
    async_client: AsyncClient,
    _auth_ok: None,
) -> None:
    event = _event()
    event["activity"]["field"] = "name"
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 204


async def test_webhook_rejects_non_issue_event(
    async_client: AsyncClient,
    _auth_ok: None,
) -> None:
    event = _event()
    event["event"] = "module"
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 204


async def test_webhook_rejects_unknown_state_transition(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
) -> None:
    event = _event(previous_state="Proposed", current_state="In Progress")
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    # Proposed -> In Progress is not an approval-eligible transition, so ignored.
    assert response.status_code == 204


async def test_webhook_missing_task_returns_404(
    async_client: AsyncClient,
    _auth_ok: None,
) -> None:
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(issue_id="MISSING"),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 404


async def test_webhook_stale_state_returns_409(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
) -> None:
    # Task is PROPOSED but webhook claims previous Plane state was Plan Approved.
    event = _event(previous_state="Plan Approved", current_state="Approved")
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 409


async def test_webhook_rejection_reverts_plane_state(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#156: Controller rejection must set Plane issue back to previous state."""
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_api_token",
        "token",
    )
    reverts: list[tuple[str, str]] = []
    comments: list[tuple[str, str]] = []

    async def _update_state(
        _self: Any, issue_id: str, state_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        reverts.append((issue_id, state_id))
        return {}

    async def _add_comment(
        _self: Any, issue_id: str, text: str, project_id: str | None = None
    ) -> dict[str, Any]:
        comments.append((issue_id, text))
        return {}

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.update_issue_state",
        _update_state,
    )
    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.add_comment",
        _add_comment,
    )

    event = _event(previous_state="Plan Approved", current_state="Approved")
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 409
    assert reverts == [("TASK-1", _STATE_UUIDS["Plan Approved"])]
    assert comments and "Controller rejected state change" in comments[0][1]


async def test_webhook_rejection_comment_reaches_plane_even_if_revert_fails(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#357: a revert-call failure must not suppress the rejection comment."""
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_api_token",
        "token",
    )
    reverts: list[tuple[str, str]] = []
    comments: list[tuple[str, str]] = []

    async def _update_state(
        _self: Any, issue_id: str, state_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        reverts.append((issue_id, state_id))
        raise RuntimeError("Plane API timeout")

    async def _add_comment(
        _self: Any, issue_id: str, text: str, project_id: str | None = None
    ) -> dict[str, Any]:
        comments.append((issue_id, text))
        return {}

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.update_issue_state",
        _update_state,
    )
    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.add_comment",
        _add_comment,
    )

    event = _event(previous_state="Plan Approved", current_state="Approved")
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 409
    assert reverts == [("TASK-1", _STATE_UUIDS["Plan Approved"])]
    assert comments and "Controller rejected state change" in comments[0][1]


async def test_webhook_plan_approval_advances_state(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
) -> None:
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 204

    refreshed = await seeded_db.scalar(
        select(Task).where(Task.id == "TASK-1")  # type: ignore[arg-type]
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.PLAN_APPROVED


async def test_webhook_self_approval_returns_403(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
) -> None:
    # task.proposed_by is "agent-1"; actor email is "agent-1" -> self-approval.
    event = _event(actor={"id": "user-uuid-2", "email": "agent-1"})
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 403


async def test_webhook_rejects_missing_secret(
    async_client: AsyncClient,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # secret is configured by _auth_ok, but request does not present it.
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", "secret"
    )
    response = await async_client.post("/webhooks/plane", json=_event())
    assert response.status_code == 401


async def test_webhook_accepts_valid_secret(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
) -> None:
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 204


async def test_webhook_rejects_actor_not_in_allowed_list(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authenticated webhook from an actor not on the allow-list is rejected."""
    monkeypatch.setattr("governance_controller.config.settings.plane_base_url", "")
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", "secret"
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_allowed_actors",
        "allowed@example.com",
    )
    # With Plane disabled, resolver must return None so the unresolvable-actor
    # guard rejects instead of silently approving.
    async def _resolve_none(*args: Any, **kwargs: Any) -> str | None:
        return None

    monkeypatch.setattr(
        "governance_controller.api.webhooks._resolve_actor_email",
        _resolve_none,
    )
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 403


async def test_webhook_rejects_ambiguous_display_name_match(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#367: a display-name collision must fail closed, not resolve to the
    first match. An attacker who renames their own Plane ``display_name`` to
    match an allow-listed approver's must not have their webhook attributed
    to that approver, even if the real approver happens to sort first in the
    member list.
    """
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_base_url",
        "http://plane.example.com",
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", "secret"
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_allowed_actors",
        "admin@example.com",
    )
    # _auth_ok replaces _resolve_actor_email with a fake resolver; undo that
    # so this test exercises the real ambiguous-match logic under test.
    monkeypatch.setattr(
        webhooks_module, "_resolve_actor_email", _REAL_RESOLVE_ACTOR_EMAIL
    )

    async def _colliding_members(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "results": [
                {"email": "admin@example.com", "display_name": "Jane Doe"},
                {"email": "attacker@example.com", "display_name": "Jane Doe"},
            ]
        }

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.list_workspace_members",
        _colliding_members,
    )

    event = _event(actor={"id": "attacker-uuid", "display_name": "Jane Doe"})
    response = await async_client.post(
        "/webhooks/plane",
        json=event,
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 403

    refreshed = await seeded_db.scalar(
        select(Task).where(Task.id == "TASK-1")  # type: ignore[arg-type]
    )
    assert refreshed is not None
    assert refreshed.state == TaskState.PROPOSED


async def test_webhook_rejects_unresolvable_actor(
    async_client: AsyncClient,
    seeded_db: AsyncSession,
    _auth_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_base_url",
        "http://plane.example.com",
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_secret", "secret"
    )
    monkeypatch.setattr(
        "governance_controller.config.settings.plane_webhook_allowed_actors",
        "allowed@example.com",
    )
    # Force member lookup to return no match so the allowed-list check rejects.
    async def _no_members(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": []}

    monkeypatch.setattr(
        "governance_controller.adapters.plane_client.PlaneClient.list_workspace_members",
        _no_members,
    )
    response = await async_client.post(
        "/webhooks/plane",
        json=_event(),
        headers={"X-Plane-Webhook-Secret": "secret"},
    )
    assert response.status_code == 403
