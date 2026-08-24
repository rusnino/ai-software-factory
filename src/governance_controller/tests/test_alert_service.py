"""Tests for the alert and verification feedback service."""

from typing import Any

import pytest

from governance_controller.constants import TaskState
from governance_controller.models.task import Task
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.alert_service import AlertService


class _FakePlaneClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def add_comment(
        self, issue_id: str, text: str, project_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("add_comment", (issue_id, text, project_id), {}))
        return {"id": "comment-1"}


@pytest.fixture
def fake_plane() -> _FakePlaneClient:
    return _FakePlaneClient()


def _task(state: TaskState = TaskState.AGENT_REVIEW) -> Task:
    return Task(
        id="TASK-1",
        project_id="proj-1",
        state=state,
        proposed_by="agent-1",
        execution_attempts=2,
    )


def _contract() -> TaskContract:
    return TaskContract(
        task_id="TASK-1",
        project_id="proj-1",
        proposed_by="agent-1",
        objective="Implement feature",
        acceptance=["Tests pass"],
        execution=ExecutionConfig(max_retries=2),
    )


async def test_verification_failure_feedback_posts_plane_comment(
    fake_plane: _FakePlaneClient,
) -> None:
    service = AlertService(plane_client=fake_plane)
    report = {
        "passed": False,
        "checks": [
            {
                "name": "required:test",
                "status": "failed",
                "command": "pytest",
                "expected_exit": 0,
                "actual_exit": 1,
                "stderr": "Assertion failed",
            }
        ],
    }

    await service.notify_verification_failure(
        task=_task(),
        contract=_contract(),
        report=report,
        attempt=2,
        max_retries=2,
    )

    assert len(fake_plane.calls) == 1
    assert fake_plane.calls[0][0] == "add_comment"
    assert "TASK-1" in fake_plane.calls[0][1][1]
    assert "pytest" in fake_plane.calls[0][1][1]


async def test_terminal_failure_alert_posts_plane_comment(
    fake_plane: _FakePlaneClient,
) -> None:
    service = AlertService(plane_client=fake_plane)
    report = {"passed": False, "checks": []}

    await service.notify_terminal_failure(
        task=_task(TaskState.FAILED),
        contract=_contract(),
        report=report,
        reason="max_retries_exhausted",
    )

    assert len(fake_plane.calls) == 1
    assert fake_plane.calls[0][0] == "add_comment"
    assert "Terminal failure" in fake_plane.calls[0][1][1]


async def test_without_plane_config_only_logs(
    fake_plane: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    service = AlertService()

    await service.notify_verification_failure(
        task=_task(),
        contract=_contract(),
        report={"passed": False, "checks": []},
        attempt=1,
        max_retries=2,
    )

    assert len(fake_plane.calls) == 0


async def test_plane_comment_failure_is_swallowed(
    fake_plane: _FakePlaneClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_exc(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Plane down")

    fake_plane.add_comment = raise_exc
    service = AlertService(plane_client=fake_plane)

    # Should not raise.
    await service.notify_terminal_failure(
        task=_task(TaskState.FAILED),
        contract=_contract(),
        report={"passed": False, "checks": []},
        reason="plane_unreachable",
    )

    assert True
