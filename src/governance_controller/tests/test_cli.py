"""Tests for the governance_controller CLI approval command."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from governance_controller.cli import app


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
