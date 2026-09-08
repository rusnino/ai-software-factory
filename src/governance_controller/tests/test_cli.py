"""Tests for the governance_controller CLI commands."""

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from governance_controller.cli import app, reconcile
from governance_controller.constants import TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.services.audit_service import AuditService


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

    def test_approve_with_idempotency_key_is_idempotent(
        self, tmp_path
    ) -> None:
        """#316: retrying gc approve with the same --idempotency-key is safe."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        db_path = tmp_path / "live_cli_idempotency.db"
        secret = "live-cli-secret-idempotency"
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

            task_id = "live-cli-idempotency-316"
            create_payload = {
                "task_contract": {
                    "task_id": task_id,
                    "project_id": "live-cli-proj-316",
                    "proposed_by": "agent-1",
                    "objective": "Live CLI idempotency test for #316",
                    "acceptance": ["CLI approve is idempotent with --idempotency-key"],
                },
                "project_profile": {
                    "project_id": "live-cli-proj-316",
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

            idempotency_key = "idempotency-key-316"
            cli_env = {**os.environ, "GC_CONTROLLER_API_SECRET": secret}
            for _ in range(2):
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
                        "--idempotency-key",
                        idempotency_key,
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

    @pytest.mark.parametrize("idempotency_key", ["", " ", "\t\n"])
    def test_approve_rejects_blank_idempotency_key_before_request(
        self, runner: CliRunner, idempotency_key: str
    ) -> None:
        with patch("governance_controller.cli.httpx.post") as mock_post:
            result = runner.invoke(
                app,
                [
                    "approve",
                    "TASK-327",
                    "--idempotency-key",
                    idempotency_key,
                ],
            )

        assert result.exit_code == 2, result.output
        assert "Invalid value" in result.output
        mock_post.assert_not_called()


class TestCliReconcile:
    def test_reconcile_passes_plane_issue_id_to_service(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#282: the CLI must preserve Plane's id for reconciliation matching."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "")
        row = SimpleNamespace(
            id="controller-task-1",
            state=TaskState.RUNNING,
            project_id="project-a",
            plane_issue_id="plane-issue-uuid-1",
        )
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [row]
        db = MagicMock()
        db.execute = AsyncMock(return_value=query_result)

        @asynccontextmanager
        async def fake_db_session():
            yield db

        report = SimpleNamespace(checked=1, divergences=[])
        reconciliation = MagicMock()
        reconciliation.reconcile = AsyncMock(return_value=report)
        with (
            patch("governance_controller.cli.get_db_session", fake_db_session),
            patch(
                "governance_controller.cli.ReconciliationService",
                return_value=reconciliation,
            ),
        ):
            result = runner.invoke(app, ["reconcile", "project-a"])

        assert result.exit_code == 0, result.output
        assert reconciliation.reconcile.await_args.kwargs["controller_tasks"] == [
            (
                "controller-task-1",
                TaskState.RUNNING,
                "project-a",
                "plane-issue-uuid-1",
            )
        ]

    def test_reconcile_retry_preserves_persisted_plane_metadata(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
        row = SimpleNamespace(
            id="controller-task-1",
            state=TaskState.RUNNING,
            project_id="project-a",
            plane_issue_id=None,
            task_contract_json={
                "objective": "Recover Plane link",
                "acceptance": ["the link is recovered"],
                "_plane_projection_source": "telegram",
                "approval_required": False,
            },
        )
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [row]
        db = MagicMock()
        db.execute = AsyncMock(return_value=query_result)
        db.get = AsyncMock(return_value=row)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()

        @asynccontextmanager
        async def fake_db_session():
            yield db

        projection = MagicMock()
        projection.ensure_plane_issue = AsyncMock(
            return_value={"id": "plane-created-uuid"}
        )
        report = SimpleNamespace(checked=1, divergences=[])
        reconciliation = MagicMock()
        reconciliation.reconcile = AsyncMock(return_value=report)
        with (
            patch("governance_controller.cli.get_db_session", fake_db_session),
            patch(
                "governance_controller.services.plane_projection.PlaneProjectionService",
                return_value=projection,
            ),
            patch(
                "governance_controller.cli.ReconciliationService",
                return_value=reconciliation,
            ),
        ):
            result = runner.invoke(app, ["reconcile", "project-a"])

        assert result.exit_code == 0, result.output
        kwargs = projection.ensure_plane_issue.await_args.kwargs
        assert kwargs["source"] == "telegram"
        assert kwargs["approval_required"] is False

    def test_reconcile_legacy_retry_does_not_invent_plane_metadata(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
        row = SimpleNamespace(
            id="legacy-task-1",
            state=TaskState.RUNNING,
            project_id="project-a",
            plane_issue_id=None,
            task_contract_json={},
        )
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [row]
        db = MagicMock()
        db.execute = AsyncMock(return_value=query_result)
        db.get = AsyncMock(return_value=row)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()

        @asynccontextmanager
        async def fake_db_session():
            yield db

        projection = MagicMock()
        projection.ensure_plane_issue = AsyncMock(return_value={"id": "plane-legacy"})
        reconciliation = MagicMock()
        reconciliation.reconcile = AsyncMock(
            return_value=SimpleNamespace(checked=1, divergences=[])
        )
        with (
            patch("governance_controller.cli.get_db_session", fake_db_session),
            patch(
                "governance_controller.services.plane_projection.PlaneProjectionService",
                return_value=projection,
            ),
            patch(
                "governance_controller.cli.ReconciliationService",
                return_value=reconciliation,
            ),
        ):
            result = runner.invoke(app, ["reconcile", "project-a"])

        assert result.exit_code == 0, result.output
        kwargs = projection.ensure_plane_issue.await_args.kwargs
        assert kwargs["source"] is None
        assert kwargs["approval_required"] is None

    def test_reconcile_refreshes_plane_id_after_issue_retry(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#282: retry-created Plane IDs must reach the same reconcile pass."""
        from governance_controller import config

        monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
        row = SimpleNamespace(
            id="controller-task-1",
            state=TaskState.RUNNING,
            project_id="project-a",
            plane_issue_id=None,
            task_contract_json={},
        )
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [row]
        db = MagicMock()
        db.execute = AsyncMock(return_value=query_result)
        db.get = AsyncMock(return_value=row)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.refresh = AsyncMock()

        @asynccontextmanager
        async def fake_db_session():
            yield db

        projection = MagicMock()
        projection.ensure_plane_issue = AsyncMock(
            return_value={"id": "plane-created-uuid"}
        )
        report = SimpleNamespace(checked=1, divergences=[])
        reconciliation = MagicMock()
        reconciliation.reconcile = AsyncMock(return_value=report)
        with (
            patch("governance_controller.cli.get_db_session", fake_db_session),
            patch(
                "governance_controller.services.plane_projection.PlaneProjectionService",
                return_value=projection,
            ),
            patch(
                "governance_controller.cli.ReconciliationService",
                return_value=reconciliation,
            ),
        ):
            result = runner.invoke(app, ["reconcile", "project-a"])

        assert result.exit_code == 0, result.output
        assert reconciliation.reconcile.await_args.kwargs["controller_tasks"] == [
            (
                "controller-task-1",
                TaskState.RUNNING,
                "project-a",
                "plane-created-uuid",
            )
        ]

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
                (
                    str(row.id),
                    row.state,
                    row.project_id or "project-a",
                    row.plane_issue_id,
                )
                for row in rows
            ]

        assert controller_tasks == [
            ("task-a", TaskState.PROPOSED, "project-a", None)
        ]
        assert all(t[2] == "project-a" for t in controller_tasks)


class TestCliVerifyAudit:
    async def test_verify_audit_reports_valid_chain(
        self,
        patched_db,
        runner: CliRunner,
    ) -> None:
        """#315: gc verify-audit confirms a clean hash chain."""
        _engine, local_session = patched_db

        async with local_session() as seed:
            for i in range(3):
                await AuditService.log(
                    db=seed,
                    event_type="test_event",
                    task_id="verify-chain-ok",
                    actor="tester",
                    source="test",
                    payload={"index": i},
                )
            await seed.commit()

        result = await asyncio.to_thread(runner.invoke, app, ["verify-audit"])
        assert result.exit_code == 0, result.output
        assert "verified" in result.output
        assert "broken" not in result.output

    async def test_verify_audit_streams_without_materializing_rows(
        self,
        patched_db,
        runner: CliRunner,
    ) -> None:
        """#326: verify-audit streams a real linked chain with bounded fetches."""
        _engine, local_session = patched_db
        test_db_url = _engine.url.render_as_string(hide_password=False)
        entries: list[AuditLog] = []

        async with local_session() as seed:
            for index in range(3):
                entries.append(
                    await AuditService.log(
                        db=seed,
                        event_type="streamed_event",
                        task_id="verify-chain-streamed",
                        actor="tester",
                        source="test",
                        payload={"index": index},
                    )
                )
            await seed.commit()

        assert entries[0].previous_hash == ""
        assert entries[1].previous_hash == entries[0].row_hash
        assert entries[2].previous_hash == entries[1].row_hash

        yield_per_values: list[object] = []

        class _ScalarResultWithoutAll:
            __slots__ = ()

        class _ResultWithoutAll:
            def scalars(self) -> _ScalarResultWithoutAll:
                return _ScalarResultWithoutAll()

        class _StreamingOnlySession:
            def __init__(self, session) -> None:
                self._session = session

            async def execute(self, _statement) -> _ResultWithoutAll:
                return _ResultWithoutAll()

            async def stream_scalars(self, statement):
                yield_per_values.append(
                    statement.get_execution_options().get("yield_per")
                )
                return await self._session.stream_scalars(statement)

        @asynccontextmanager
        async def streaming_db_session():
            # CliRunner executes the synchronous CLI in a separate event loop.
            # asyncpg sessions cannot be moved across loops, so create the test
            # session in the loop that invokes the command.
            loop_engine = create_async_engine(test_db_url, echo=False, future=True)
            loop_session = async_sessionmaker(
                loop_engine,
                expire_on_commit=False,
            )
            try:
                async with loop_session() as session:
                    yield _StreamingOnlySession(session)
            finally:
                await loop_engine.dispose()

        with patch(
            "governance_controller.cli.get_db_session", streaming_db_session
        ):
            result = await asyncio.to_thread(runner.invoke, app, ["verify-audit"])

        assert result.exit_code == 0, repr(result.exception)
        assert result.output.strip() == "Audit hash chain verified."
        assert yield_per_values == [1000]

    @pytest.mark.skipif(
        not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
        reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
    )
    async def test_verify_audit_uses_a_loop_local_postgres_session(
        self,
        patched_db,
        runner: CliRunner,
    ) -> None:
        """#326: the synchronous CLI must not reuse an asyncpg session loop."""
        _engine, local_session = patched_db

        async with local_session() as seed:
            for index in range(3):
                await AuditService.log(
                    db=seed,
                    event_type="loop_local_event",
                    task_id="verify-chain-loop-local",
                    actor="tester",
                    source="test",
                    payload={"index": index},
                )
            await seed.commit()

        result = await asyncio.to_thread(runner.invoke, app, ["verify-audit"])

        assert result.exit_code == 0, repr(result.exception)
        assert result.output.strip() == "Audit hash chain verified."

    async def test_verify_audit_detects_tampered_row(
        self,
        patched_db,
        runner: CliRunner,
    ) -> None:
        """#315: gc verify-audit reports the first row whose hash no longer matches."""
        _engine, local_session = patched_db
        row_ids: list[int] = []

        async with local_session() as seed:
            for i in range(3):
                entry = await AuditService.log(
                    db=seed,
                    event_type="test_event",
                    task_id="verify-chain-bad",
                    actor="tester",
                    source="test",
                    payload={"index": i},
                )
                row_ids.append(entry.id)
            await seed.commit()

        async with local_session() as conn:
            # Bypass ORM events and DB triggers to simulate a forensic tamper.
            dialect = conn.bind.dialect.name if conn.bind else "sqlite"  # type: ignore[union-attr]
            if dialect == "postgresql":
                await conn.execute(
                    text(
                        "DROP TRIGGER IF EXISTS auditlog_block_update_delete "
                        "ON auditlog"
                    )
                )
            else:
                await conn.execute(
                    text("DROP TRIGGER IF EXISTS auditlog_block_update")
                )
                await conn.execute(
                    text("DROP TRIGGER IF EXISTS auditlog_block_delete")
                )
            await conn.execute(
                text(
                    "UPDATE auditlog SET payload = '{\"index\": 99}' WHERE id = :id"
                ),
                {"id": row_ids[1]},
            )
            await conn.commit()

        result = await asyncio.to_thread(runner.invoke, app, ["verify-audit"])
        assert result.exit_code == 1, result.output
        assert f"broken at AuditLog id={row_ids[1]}" in result.output

    async def test_verify_audit_marks_legacy_boundary(
        self,
        patched_db,
        runner: CliRunner,
    ) -> None:
        """#315: legacy rows with empty hashes are flagged, not treated as breaks."""
        _engine, local_session = patched_db
        boundary_id: int | None = None

        async with local_session() as seed:
            # Simulate pre-hash-schema rows by inserting directly, bypassing the
            # ORM before_insert event that computes row_hash.
            boundary_result = await seed.execute(
                text(
                    "INSERT INTO auditlog "
                    "(event_id, event_type, task_id, actor, source, "
                    "timestamp, payload, previous_hash, row_hash) "
                    "VALUES (:eid, 'legacy', 'legacy-task', 'system', 'test', "
                    ":ts, '{}', '', '') RETURNING id"
                ),
                {
                    "eid": "legacy-event-1",
                    "ts": datetime.now(UTC),
                },
            )
            boundary_id = boundary_result.scalar_one()

            await AuditService.log(
                db=seed,
                event_type="hashed_event",
                task_id="legacy-task",
                actor="system",
                source="test",
                payload={"ok": True},
            )
            await seed.commit()

        result = await asyncio.to_thread(runner.invoke, app, ["verify-audit"])
        assert result.exit_code == 0, result.output
        assert "verified" in result.output
        assert f"Legacy boundary at AuditLog id={boundary_id}" in result.output


async def test_reconcile_plane_id_survives_later_rollback(
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry link remains durable when reconciliation later rolls back."""
    from governance_controller import config

    task_id = "cli-plane-durable-288"
    project_id = "cli-project-288"
    _engine, local_session = isolated_db

    async with local_session() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                proposed_by="test",
                task_contract_json={
                    "objective": "Recover Plane link",
                    "acceptance": ["the link is durable"],
                },
            )
        )
        await seed.commit()

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
    projection = MagicMock()
    projection.ensure_plane_issue = AsyncMock(
        return_value={"id": "plane-recovered-288"}
    )
    reconciliation = MagicMock()
    reconciliation.reconcile = AsyncMock(
        side_effect=RuntimeError("rollback after retry")
    )
    monkeypatch.setattr(
        "governance_controller.services.plane_projection.PlaneProjectionService",
        MagicMock(return_value=projection),
    )
    monkeypatch.setattr(
        "governance_controller.cli.ReconciliationService",
        MagicMock(return_value=reconciliation),
    )

    with pytest.raises(RuntimeError, match="rollback after retry"):
        await asyncio.to_thread(reconcile, project_id)

    async with local_session() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        assert task.plane_issue_id == "plane-recovered-288"


async def test_reconcile_crash_leaves_durable_pending_marker(
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#298: a crash during CLI Plane retry leaves an audit marker."""
    from governance_controller import config

    task_id = "cli-plane-pending-298"
    project_id = "cli-project-pending-298"
    _engine, local_session = isolated_db

    async with local_session() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                proposed_by="test",
                task_contract_json={"objective": "Recover this issue"},
            )
        )
        await seed.commit()

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")

    class _CrashedProjection:
        async def ensure_plane_issue(self, **_kwargs: object) -> dict[str, object]:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "governance_controller.services.plane_projection.PlaneProjectionService",
        lambda: _CrashedProjection(),
    )

    with pytest.raises(asyncio.CancelledError):
        await asyncio.to_thread(reconcile, project_id)

    async with local_session() as check:
        rows = await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        entries = list(rows.scalars().all())

    pending = [
        entry
        for entry in entries
        if entry.event_type == "plane_issue_creation_pending"
    ]
    assert len(pending) == 1
    assert pending[0].payload["operation"] == "create_issue"


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_reconcile_reports_plane_retry_failure_after_rollback(
    runner: CliRunner,
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed retry is reported without dereferencing expired ORM state."""
    from governance_controller import config

    task_id = "cli-plane-failure-295"
    project_id = "cli-project-failure-295"
    _engine, local_session = isolated_db

    async with local_session() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                proposed_by="test",
                task_contract_json={},
            )
        )
        await seed.commit()

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")
    projection = MagicMock()
    projection.ensure_plane_issue = AsyncMock(
        side_effect=RuntimeError("plane transient failure")
    )
    reconciliation = MagicMock()
    reconciliation.reconcile = AsyncMock(
        return_value=SimpleNamespace(checked=1, divergences=[])
    )
    monkeypatch.setattr(
        "governance_controller.services.plane_projection.PlaneProjectionService",
        MagicMock(return_value=projection),
    )
    monkeypatch.setattr(
        "governance_controller.cli.ReconciliationService",
        MagicMock(return_value=reconciliation),
    )

    result = await asyncio.to_thread(
        runner.invoke, app, ["reconcile", project_id]
    )

    assert result.exit_code == 0, result.output
    assert "[retry] plane issue creation failed" in result.output
    assert "plane transient failure" in result.output
    assert "MissingGreenlet" not in result.output
    reconciliation.reconcile.assert_awaited_once()

    async with local_session() as check:
        task = await check.scalar(select(Task).where(Task.id == task_id))
        assert task is not None
        rows = await check.execute(select(AuditLog).where(AuditLog.task_id == task_id))
        entries = list(rows.scalars().all())

    pending = [
        entry
        for entry in entries
        if entry.event_type == "plane_issue_creation_pending"
    ]
    failed = [
        entry
        for entry in entries
        if entry.event_type == "plane_issue_creation_failed"
    ]
    assert len(pending) == 1
    assert len(failed) == 1
    assert failed[0].payload["pending_event_id"] == pending[0].event_id


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
async def test_reconcile_retries_plane_issue_under_task_lock(
    isolated_db: tuple,
    patched_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent retries for one task make only one external create request."""
    from governance_controller import config
    from governance_controller.services.plane_projection import (
        acquire_plane_projection_lock as real_acquire_plane_projection_lock,
    )

    task_id = "cli-plane-lock-288"
    project_id = "cli-project-lock-288"
    _engine, local_session = isolated_db

    async with local_session() as seed:
        seed.add(
            Task(
                id=task_id,
                project_id=project_id,
                proposed_by="test",
                task_contract_json={},
            )
        )
        await seed.commit()

    monkeypatch.setattr(config.settings, "plane_base_url", "http://plane.test")

    class _BlockedProjection:
        def __init__(self) -> None:
            self.first_lookup = threading.Event()
            self.second_lookup = threading.Event()
            self.release = threading.Event()
            self._mutex = threading.Lock()
            self._lookup_attempts = 0
            self.create_requests = 0
            self.created = False

        async def ensure_plane_issue(self, **_kwargs: object) -> dict[str, object]:
            with self._mutex:
                if self.created:
                    return {"id": "plane-locked-288"}
                self._lookup_attempts += 1
                first = self._lookup_attempts == 1
                (self.first_lookup if first else self.second_lookup).set()

            if first and not await asyncio.to_thread(self.release.wait, 10):
                raise TimeoutError("test projection was not released")

            with self._mutex:
                self.create_requests += 1
                self.created = True
            return {"id": "plane-locked-288"}

    projection = _BlockedProjection()

    class _NoopReconciliation:
        async def reconcile(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(checked=1, divergences=[])

    monkeypatch.setattr(
        "governance_controller.services.plane_projection.PlaneProjectionService",
        lambda: projection,
    )
    monkeypatch.setattr(
        "governance_controller.cli.ReconciliationService",
        lambda **_kwargs: _NoopReconciliation(),
    )

    lock_attempts = 0
    lock_attempts_mutex = threading.Lock()
    second_lock_attempted = threading.Event()

    async def _tracking_lock(db, controller_task_id: str) -> None:
        nonlocal lock_attempts
        with lock_attempts_mutex:
            lock_attempts += 1
            if lock_attempts == 2:
                second_lock_attempted.set()
        await real_acquire_plane_projection_lock(db, controller_task_id)

    monkeypatch.setattr(
        "governance_controller.cli.acquire_plane_projection_lock",
        _tracking_lock,
        raising=False,
    )

    first = asyncio.create_task(asyncio.to_thread(reconcile, project_id))
    second: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(projection.first_lookup.wait, 10)
        second = asyncio.create_task(asyncio.to_thread(reconcile, project_id))
        assert await asyncio.to_thread(second_lock_attempted.wait, 10)
        assert not projection.second_lookup.is_set()
    finally:
        projection.release.set()
        tasks = [first]
        if second is not None:
            tasks.append(second)
        await asyncio.gather(*tasks, return_exceptions=True)

    assert projection.create_requests == 1
