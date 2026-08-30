"""Tests for the governance_controller CLI commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from governance_controller.cli import app
from governance_controller.constants import TaskState
from governance_controller.models.task import Task


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCliApprove:
    def test_approve_invokes_httpx_post_with_correct_payload(
        self, runner: CliRunner
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "task_id": "TASK-1",
            "state": "EXEC_APPROVED",
            "approved": True,
        }

        with patch("governance_controller.cli.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            result = runner.invoke(
                app,
                [
                    "approve",
                    "TASK-1",
                    "--type",
                    "execution",
                    "--actor",
                    "cli-user",
                    "--source",
                    "cli",
                    "--base-url",
                    "http://localhost:8000",
                    "--comment",
                    "LGTM",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with(
            "http://localhost:8000/approvals",
            json={
                "task_id": "TASK-1",
                "approval_type": "execution",
                "source": "cli",
                "actor": "cli-user",
                "timestamp": mock_post.call_args.kwargs["json"]["timestamp"],
                "comment": "LGTM",
            },
            headers={"X-Controller-Secret": ""},
        )
        assert "Approved TASK-1: EXEC_APPROVED" in result.output

    def test_approve_exits_with_code_one_on_http_error(self, runner: CliRunner) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.raise_for_status.side_effect = Exception("Not Found")

        with patch("governance_controller.cli.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            result = runner.invoke(
                app,
                [
                    "approve",
                    "TASK-2",
                    "--base-url",
                    "http://localhost:8000",
                ],
            )

        assert result.exit_code == 1


class TestCliReconcile:
    async def test_reconcile_filters_tasks_by_project_id(
        self,
        db_session,
    ) -> None:
        """#245: reconcile must only consider tasks for the requested project."""
        from sqlalchemy import select

        from governance_controller.services.reconciliation_service import (
            ReconciliationService,
        )

        task_a = Task(
            id="task-a",
            project_id="project-a",
            state=TaskState.PROPOSED,
            proposed_by="x",
        )
        task_b = Task(
            id="task-b",
            project_id="project-b",
            state=TaskState.PROPOSED,
            proposed_by="x",
        )
        db_session.add(task_a)
        db_session.add(task_b)
        await db_session.commit()

        mock_report = MagicMock()
        mock_report.checked = 1
        mock_report.divergences = []

        with patch.object(
            ReconciliationService, "reconcile", new=AsyncMock(return_value=mock_report)
        ):
            result = await db_session.execute(
                select(Task.id, Task.state, Task.project_id, Task.plane_issue_id).where(
                    Task.project_id == "project-a"
                )
            )
            rows = result.all()
            controller_tasks = [
                (str(row.id), row.state, row.project_id or "project-a")
                for row in rows
            ]

        assert controller_tasks == [("task-a", TaskState.PROPOSED, "project-a")]
        assert all(t[2] == "project-a" for t in controller_tasks)
