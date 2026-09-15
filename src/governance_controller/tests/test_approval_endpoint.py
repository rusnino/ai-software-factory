"""Tests for the approvals REST API endpoint."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from governance_controller.adapters.macro_agent.executor import MacroAgentExecutor
from governance_controller.api.approvals import (
    _make_idempotency_key,
    get_approval_service,
)
from governance_controller.constants import ApprovalType, TaskState
from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.schemas import ProjectProfile, RepositoryConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.permission_service import PermissionService


@pytest.fixture
def sample_contract() -> TaskContract:
    return TaskContract(
        task_id="approval-task-1",
        project_id="approval-proj-1",
        proposed_by="agent-1",
        objective="Test approvals via API",
        acceptance=["Approvals advance state"],
    )


@pytest.fixture
def sample_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="approval-proj-1",
        project_name="Approval Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )


@pytest.fixture
def mock_executor() -> AsyncMock:
    return AsyncMock(spec=MacroAgentExecutor)


@pytest_asyncio.fixture
async def async_client(client_db_session, mock_executor) -> AsyncClient:
    async def _override_get_db():
        yield client_db_session

    def _override_get_approval_service(db=Depends(get_db)) -> ApprovalService:
        # This endpoint test suite exercises state-machine/policy behavior via
        # the real HTTP route, not the X-Human-Approval-Secret header itself
        # (that is covered separately) -- assume the human-approval proof
        # (#376) was already presented so these tests keep testing what they
        # were written to test.
        return ApprovalService(
            db=db,
            executor=mock_executor,
            permission_service=PermissionService(human_approval_verified=True),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_approval_service] = _override_get_approval_service
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_approval_service, None)


@pytest.fixture(autouse=True)
def _configure_controller_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "governance_controller.config.settings.controller_api_secret",
        _CONTROLLER_SECRET,
    )


_CONTROLLER_SECRET = "controller-secret"


async def _create_task(
    client: AsyncClient,
    contract: TaskContract,
    profile: ProjectProfile,
) -> None:
    payload = {
        "task_contract": contract.model_dump(),
        "project_profile": profile.model_dump(),
    }
    response = await client.post(
        "/tasks",
        json=payload,
        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
    )
    assert response.status_code == 201


def _approval_payload(
    task_id: str, approval_type: ApprovalType, actor: str = "admin"
) -> dict:
    return {
        "task_id": task_id,
        "approval_type": approval_type.value,
        "source": "test",
        "actor": actor,
        "timestamp": "2026-08-18T12:00:00+00:00",
    }


class TestGetApprovalServiceHumanApprovalSecret:
    """#376: `get_approval_service` (api/approvals.py:27's construction site)

    must derive `human_approval_verified` from a real per-request secret
    comparison, not from mere presence of `admins`/`known_proposers`
    configuration -- see PermissionService's own regression coverage for why
    name allow-lists alone were insufficient (GitHub-reopened #376 residual).
    """

    def test_verified_false_when_secret_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "governance_controller.config.settings.human_approval_secret", ""
        )
        service = get_approval_service(
            db=None,  # type: ignore[arg-type]
            x_human_approval_secret="anything",
        )
        assert service.permission_service.human_approval_verified is False

    def test_verified_false_when_header_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "governance_controller.config.settings.human_approval_secret",
            "top-secret",
        )
        service = get_approval_service(db=None, x_human_approval_secret=None)  # type: ignore[arg-type]
        assert service.permission_service.human_approval_verified is False

    def test_verified_false_when_header_does_not_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "governance_controller.config.settings.human_approval_secret",
            "top-secret",
        )
        service = get_approval_service(
            db=None,  # type: ignore[arg-type]
            x_human_approval_secret="wrong-secret",
        )
        assert service.permission_service.human_approval_verified is False

    def test_verified_true_when_header_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "governance_controller.config.settings.human_approval_secret",
            "top-secret",
        )
        service = get_approval_service(
            db=None,  # type: ignore[arg-type]
            x_human_approval_secret="top-secret",
        )
        assert service.permission_service.human_approval_verified is True


class TestApprovalEndpoint:
    async def test_approval_without_previous_plan_returns_409(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        payload = _approval_payload("approval-task-1", ApprovalType.EXECUTION)

        response = await async_client.post(
            "/approvals",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 409
        assert "PROPOSED -> EXEC_APPROVED" in response.text

    async def test_approval_plan_advances_state(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        payload = _approval_payload("approval-task-1", ApprovalType.PLAN)

        response = await async_client.post(
            "/approvals",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["task_id"] == "approval-task-1"
        assert body["state"] == TaskState.PLAN_APPROVED.value
        assert body["approved"] is True

    async def test_approval_execution_advances_state(
        self,
        async_client: AsyncClient,
        mock_executor: AsyncMock,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        mock_executor.start.return_value = {"run_id": "run-123"}

        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.EXECUTION),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == TaskState.RUNNING.value
        assert body["approved"] is True
        mock_executor.start.assert_awaited_once()
        call_args, _ = mock_executor.start.await_args
        assert len(call_args) == 2
        assert call_args[1] is not None

    async def test_approval_merge_advances_to_done(
        self,
        async_client: AsyncClient,
        client_db_session,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        # Drive the task to HUMAN_REVIEW via direct state changes so merge can fire.
        from governance_controller.models.task import Task
        from governance_controller.services.state_machine import StateMachine

        result = await client_db_session.execute(
            select(Task).where(Task.id == "approval-task-1")
        )
        task = result.scalar_one()
        StateMachine.transition(task, TaskState.EXEC_APPROVED)
        StateMachine.transition(task, TaskState.READY)
        StateMachine.transition(task, TaskState.RUNNING)
        StateMachine.transition(task, TaskState.AGENT_REVIEW)
        StateMachine.transition(task, TaskState.HUMAN_REVIEW)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.MERGE),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == TaskState.DONE.value
        assert body["approved"] is True

    async def test_policy_violation_returns_403(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        sample_contract.execution.harness = "forbidden-harness"
        await _create_task(async_client, sample_contract, sample_profile)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 403
        body = response.json()
        assert "violations" in body["detail"]
        assert isinstance(body["detail"]["violations"], list)

    async def test_self_approval_returns_403(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload(
                "approval-task-1", ApprovalType.PLAN, actor="agent-1"),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 403
        body = response.json()
        assert "violations" in body["detail"]
        assert any(
            "cannot approve their own task" in v for v in body["detail"]["violations"]
        )

    async def test_permission_denied_returns_403(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload(
                "approval-task-1", ApprovalType.PLAN, actor="system"),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 403
        body = response.json()
        assert "violations" in body["detail"]
        assert any("may not request" in v for v in body["detail"]["violations"])

    async def test_policy_violation_with_comma_preserved(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        """GAP-100 regression: violations containing commas remain whole."""
        sample_profile.security.forbidden_paths = ["/secure/a,b"]
        sample_contract.inputs = ["/secure/a,b/c"]
        await _create_task(async_client, sample_contract, sample_profile)

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 403
        violations = response.json()["detail"]["violations"]
        assert any("/secure/a,b" in v for v in violations)
        assert sum("/secure/a" in v for v in violations) == 1

    async def test_invalid_timestamp_returns_400(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        await _create_task(async_client, sample_contract, sample_profile)
        payload = _approval_payload("approval-task-1", ApprovalType.PLAN)
        payload["timestamp"] = "not-a-timestamp"

        response = await async_client.post(
            "/approvals",
            json=payload,
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 400

    async def test_executor_start_failure_returns_503(
        self,
        async_client: AsyncClient,
        mock_executor: AsyncMock,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        mock_executor.start.side_effect = RuntimeError(
            "macro-agent start failed: connection refused"
        )

        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.EXECUTION),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 503
        assert "macro-agent start failed" in response.json()["detail"]

    async def test_fallback_idempotency_key_is_collision_resistant(
        self,
        async_client: AsyncClient,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        """GAP-087 regression: delimiter injection cannot create key collisions."""
        from governance_controller.constants import ApprovalType

        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        key1 = _make_idempotency_key(
            "myid|plan", ApprovalType.PLAN, "bob", "2026-08-18T12:00:00+00:00"
        )
        key2 = _make_idempotency_key(
            "myid", ApprovalType.PLAN, "plan|bob", "2026-08-18T12:00:00+00:00"
        )
        assert key1 != key2
        # SHA-256 hex strings are 64 characters.
        assert len(key1) == 64
        assert len(key2) == 64

    async def test_approval_commits_ready_before_macro_agent_start(
        self,
        async_client: AsyncClient,
        mock_executor: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        """GAP-080 regression: commit READY/Execution before the live call."""
        from sqlalchemy.ext.asyncio import AsyncSession

        commit_counts: dict[int, int] = {}
        start_called = [False]

        original_commit = AsyncSession.commit

        async def _patched_commit(self):
            commit_counts[id(self)] = commit_counts.get(id(self), 0) + 1
            return await original_commit(self)

        async def _patched_start(*args, **kwargs):
            start_called[0] = True
            # At the moment the live executor is invoked, the READY transition
            # and Execution row must already be committed (lock released).
            assert any(c >= 1 for c in commit_counts.values())
            return {"run_id": "run-committed"}

        monkeypatch.setattr(AsyncSession, "commit", _patched_commit)
        mock_executor.start.side_effect = _patched_start

        await _create_task(async_client, sample_contract, sample_profile)
        await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.PLAN),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )
        response = await async_client.post(
            "/approvals",
            json=_approval_payload("approval-task-1", ApprovalType.EXECUTION),
            headers={"X-Controller-Secret": _CONTROLLER_SECRET},
        )

        assert response.status_code == 200
        assert response.json()["state"] == TaskState.RUNNING.value
        assert start_called[0]
