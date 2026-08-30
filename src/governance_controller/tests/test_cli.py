"""Tests for the governance_controller CLI commands."""

import os
import socket
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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

    def test_approve_authenticates_against_real_running_server(
        self, tmp_path
    ) -> None:
        """#277: the mocked test above only checks that ``approve`` builds the
        right dict for a fake ``httpx.post`` — it would pass even if the real
        HTTP layer never sent the header at all (which was the actual bug: the
        CLI could not authenticate against any real deployment). This test
        starts the real FastAPI app in a subprocess, creates a task through
        the real API, and shells out to the actual CLI entrypoint with
        ``GC_CONTROLLER_API_SECRET`` set, then confirms a real HTTP 200 and a
        real state transition read back from the server/DB.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        db_path = tmp_path / "live_cli.db"
        secret = "live-cli-secret"
        base_url = f"http://127.0.0.1:{port}"
        server_env = {
            **os.environ,
            "GC_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "GC_CONTROLLER_API_SECRET": secret,
        }

        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "governance_controller.main:app",
                "--port",
                str(port),
            ],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 15
            ready = False
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    pytest.fail(
                        "Controller server exited early:\n"
                        + (server.stdout.read() if server.stdout else "")
                    )
                try:
                    resp = httpx.get(f"{base_url}/health", timeout=0.5)
                    if resp.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            if not ready:
                pytest.fail("Controller server did not become ready in time")

            task_id = "live-cli-task-277"
            create_payload = {
                "task_contract": {
                    "task_id": task_id,
                    "project_id": "live-cli-proj-277",
                    "proposed_by": "agent-1",
                    "objective": "Live CLI regression test for #277",
                    "acceptance": ["CLI approve authenticates against a real server"],
                },
                "project_profile": {
                    "project_id": "live-cli-proj-277",
                    "project_name": "Live CLI Project",
                    "repository": {"path": "/tmp/repo"},
                },
            }
            create_resp = httpx.post(
                f"{base_url}/tasks",
                json=create_payload,
                headers={"X-Controller-Secret": secret},
                timeout=5,
            )
            assert create_resp.status_code == 201, create_resp.text

            cli_env = {**os.environ, "GC_CONTROLLER_API_SECRET": secret}
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "governance_controller.cli",
                    "approve",
                    task_id,
                    "--type",
                    "plan",
                    "--base-url",
                    base_url,
                ],
                env=cli_env,
                capture_output=True,
                text=True,
                timeout=15,
            )

            assert result.returncode == 0, result.stdout + result.stderr
            assert f"Approved {task_id}: PLAN_APPROVED" in result.stdout

            get_resp = httpx.get(
                f"{base_url}/tasks/{task_id}",
                headers={"X-Controller-Secret": secret},
                timeout=5,
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["state"] == "PLAN_APPROVED"
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

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
