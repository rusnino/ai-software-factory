"""Tests for the AuditLog model and AuditService."""

import asyncio
import os
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import ExecutionConfig, TaskContract
from governance_controller.services.approval_service import ApprovalService
from governance_controller.services.audit_service import AuditService


async def _make_task(
    db: AsyncSession,
    state: TaskState = TaskState.PROPOSED,
    task_id: str = "task-1",
) -> Task:
    task = Task(id=task_id, project_id="proj-1", state=state, proposed_by="agent-1")
    db.add(task)
    await db.flush()
    return task


def _make_contract(harness: str = "opencode") -> TaskContract:
    return TaskContract(
        task_id="task-1",
        project_id="proj-1",
        proposed_by="agent-1",
        objective="Implement feature X",
        acceptance=["feature X passes tests"],
        execution=ExecutionConfig(harness=harness),
        forbidden_paths=[],
    )


def _make_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="proj-1",
        project_name="Test Project",
        repository={"path": "/tmp/repo"},
        execution={"allowed_harnesses": ["opencode"]},
        security={"forbidden_paths": []},
    )


class TestAuditLogModel:
    async def test_create_audit_log_entry(self, db_session: AsyncSession) -> None:
        entry = await AuditService.log(
            db=db_session,
            event_type="state_change",
            task_id="task-1",
            actor="human-1",
            source="plane",
            execution_id="exec-1",
            payload={"previous_state": "PROPOSED", "new_state": "PLAN_APPROVED"},
        )

        assert entry.id is not None
        assert entry.event_type == "state_change"
        assert entry.task_id == "task-1"
        assert entry.actor == "human-1"
        assert entry.source == "plane"
        assert entry.execution_id == "exec-1"
        assert entry.payload == {
            "previous_state": "PROPOSED",
            "new_state": "PLAN_APPROVED",
        }

    async def test_event_id_is_generated_uuid(self, db_session: AsyncSession) -> None:
        entry = await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-1",
            actor="human-1",
            source="telegram",
        )

        assert entry.event_id is not None and entry.event_id != ""
        uuid = UUID(entry.event_id)
        assert str(uuid) == entry.event_id

        rows = await db_session.execute(select(AuditLog))
        assert len(rows.scalars().all()) == 1

    async def test_append_only_no_update_path_exposed(
        self, db_session: AsyncSession
    ) -> None:
        entry = await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-1",
            actor="human-1",
            source="dashboard",
        )

        original_event_id = entry.event_id
        original_payload = entry.payload

        with pytest.raises(AttributeError):
            AuditService.update(db_session, entry.event_id, {"payload": {"x": 1}})

        reloaded = await db_session.get(AuditLog, entry.id)
        assert reloaded is not None
        assert reloaded.event_id == original_event_id
        assert reloaded.payload == original_payload

    async def test_hash_chain_links_rows(self, db_session: AsyncSession) -> None:
        first = await AuditService.log(
            db=db_session,
            event_type="state_change",
            task_id="task-hash",
            actor="human-1",
            source="plane",
            payload={"x": 1},
        )
        second = await AuditService.log(
            db=db_session,
            event_type="state_change",
            task_id="task-hash",
            actor="human-1",
            source="plane",
            payload={"x": 2},
        )

        assert first.row_hash is not None
        assert second.row_hash is not None
        assert first.row_hash != second.row_hash
        assert second.previous_hash == first.row_hash

        # Re-compute on a fresh load to verify stored state verifies.
        from sqlalchemy import select

        reloaded = (
            await db_session.execute(select(AuditLog).where(AuditLog.id == second.id))
        ).scalar_one()
        assert reloaded.row_hash == reloaded.compute_hash()

    async def test_compute_hash_works_on_orm_loaded_row(
        self, db_session: AsyncSession
    ) -> None:
        """#281: hash verification must work after a plain ORM SELECT."""
        entry = await AuditService.log(
            db=db_session,
            event_type="state_change",
            task_id="task-loaded-hash",
            actor="human-1",
            source="plane",
            payload={"x": 1},
        )
        entry_id = entry.id
        db_session.expunge(entry)

        reloaded = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.id == entry_id)
            )
        ).scalar_one()

        assert reloaded.row_hash == reloaded.compute_hash()

    async def test_audit_row_update_is_blocked(self, db_session: AsyncSession) -> None:
        entry = await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-1",
            actor="human-1",
            source="plane",
        )

        entry.payload = {"tampered": True}
        with pytest.raises(RuntimeError, match="append-only"):
            await db_session.commit()

    async def test_audit_row_delete_is_blocked(self, db_session: AsyncSession) -> None:
        entry = await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-1",
            actor="human-1",
            source="plane",
        )

        await db_session.delete(entry)
        with pytest.raises(RuntimeError, match="append-only"):
            await db_session.flush()

    async def test_core_bulk_update_is_blocked(self, db_session: AsyncSession) -> None:
        """#120: Core-style update() must not bypass ORM immutability events."""
        from sqlalchemy import update

        await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-core-update",
            actor="human-1",
            source="plane",
        )

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                update(AuditLog)
                .where(AuditLog.task_id == "task-core-update")
                .values(actor="tampered")
            )
            await db_session.commit()

    async def test_core_bulk_delete_is_blocked(self, db_session: AsyncSession) -> None:
        """#120: Core-style delete() must not bypass ORM immutability events."""
        from sqlalchemy import delete

        await AuditService.log(
            db=db_session,
            event_type="approval",
            task_id="task-core-delete",
            actor="human-1",
            source="plane",
        )

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                delete(AuditLog).where(AuditLog.task_id == "task-core-delete")
            )
            await db_session.commit()


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def test_get_engine_returns_different_engine_per_loop() -> None:
    """#190: engine must not be reused across different event loops."""
    import asyncio

    from governance_controller.db import get_engine

    engines: list[AsyncEngine] = []

    async def capture() -> None:
        engines.append(get_engine())

    asyncio.run(capture())
    asyncio.run(capture())

    assert len(engines) == 2
    assert engines[0] is not engines[1]


async def test_run_migrations_raises_when_auditlog_table_missing() -> None:
    """#137: run_migrations() guard raises before introspecting a missing table."""
    import asyncio

    from governance_controller.db import _engines_by_loop, run_migrations
    from governance_controller.db import engine as db_engine

    original_engine = db_engine
    original_engines = dict(_engines_by_loop)
    # Use a fresh, empty in-memory SQLite database with no create_all().
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    try:
        db_module = __import__("governance_controller.db", fromlist=["engine"])
        db_module.engine = test_engine
        _engines_by_loop[asyncio.get_running_loop()] = test_engine
        with pytest.raises(
            RuntimeError,
            match=r"run_migrations\(\) called before auditlog table exists",
        ):
            await run_migrations()
    finally:
        db_module.engine = original_engine
        _engines_by_loop.clear()
        _engines_by_loop.update(original_engines)
        await test_engine.dispose()


@pytest.mark.skipif(
    not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
class TestAuditLogPostgresDDL:
    async def test_postgres_create_all_and_run_migrations_succeed(
        self,
    ) -> None:
        """#126: literal % in PL/pgSQL must not crash create_all/run_migrations."""
        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        engine = create_async_engine(url, echo=False, future=True)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()

        # #129: run_migrations() must also complete without error.
        import asyncio

        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        test_engine = create_async_engine(url, echo=False, future=True)
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = test_engine
            # run_migrations() resolves its connection via get_engine(), which
            # is keyed by the running event loop, not the db.engine attribute
            # set above. Without seeding the per-loop cache too, get_engine()
            # falls back to settings.database_url (the module's default
            # postgres/postgres/governance credentials) instead of this
            # test's GC_TEST_DATABASE_URL, which breaks in any environment
            # (e.g. CI) where those two URLs use different credentials.
            _engines_by_loop[asyncio.get_running_loop()] = test_engine
            await run_migrations()
        finally:
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await test_engine.dispose()

    async def test_postgres_run_migrations_backfills_legacy_auditlog(
        self,
    ) -> None:
        """#129: migrate a pre-existing auditlog missing hash columns."""
        from sqlalchemy import select, text

        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        # Reuse the asyncpg-backed async engine for setup too, avoiding a
        # synchronous psycopg2 dependency in the test environment.
        legacy_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            # Each engine created in this test must be bound to the same event
            # loop as run_migrations(). Avoiding connection pooling sidesteps
            # asyncpg loop-attachment issues when tests are collected in a
            # different order.
            poolclass=NullPool,
        )
        try:
            async with legacy_engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS auditlog CASCADE"))
                await conn.execute(
                    text(
                        """
                        CREATE TABLE auditlog (
                            id SERIAL PRIMARY KEY,
                            event_id VARCHAR NOT NULL,
                            event_type VARCHAR NOT NULL,
                            task_id VARCHAR NOT NULL,
                            execution_id VARCHAR,
                            actor VARCHAR NOT NULL,
                            source VARCHAR NOT NULL,
                            timestamp TIMESTAMP WITH TIME ZONE
                                NOT NULL DEFAULT NOW(),
                            payload JSONB DEFAULT '{}'
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO auditlog "
                        "(event_id, event_type, task_id, actor, source, payload) "
                        "VALUES "
                        "('legacy-1', 'approval', 'task-legacy', "
                        "'human-1', 'plane', '{}')"
                    )
                )
        finally:
            await legacy_engine.dispose()

        # Now run the async migration path against the legacy table.
        import asyncio

        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        migration_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
        )
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = migration_engine
            # See the sibling test above: get_engine() is keyed by the
            # running event loop, not db.engine, so the per-loop cache must
            # be seeded too or run_migrations() silently falls back to
            # settings.database_url's default credentials.
            _engines_by_loop[asyncio.get_running_loop()] = migration_engine
            await run_migrations()
        finally:
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await migration_engine.dispose()

        # Verify columns were added, legacy row backfilled, and the trigger
        # now blocks updates.
        check_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
        )
        try:
            async with check_engine.begin() as conn:
                columns = (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'auditlog'"
                        )
                    )
                ).fetchall()
                column_names = {c[0] for c in columns}
                assert "previous_hash" in column_names
                assert "row_hash" in column_names

                row = (
                    await conn.execute(
                        select(text("previous_hash, row_hash")).select_from(
                            text("auditlog")
                        )
                    )
                ).fetchone()
                assert row is not None
                assert row.previous_hash == ""
                assert row.row_hash == ""

                with pytest.raises(Exception, match="append-only"):
                    await conn.execute(
                        text(
                            "UPDATE auditlog SET actor = 'tampered' "
                            "WHERE task_id = 'task-legacy'"
                        )
                    )
        finally:
            await check_engine.dispose()

    async def test_postgres_run_migrations_backfills_missing_execution_columns(
        self,
    ) -> None:
        """#268/#308: run_migrations() restores missing execution columns.

        Simulates an already-deployed Postgres instance whose ``execution``
        table predates the ``status_error`` and ``cancellation_pending``
        columns: create the full schema via ``create_all()`` (which includes
        both columns), then drop them to recreate the pre-existing-deployment
        shape, seed an unresolved cancellation marker, run the real
        ``run_migrations()``, and confirm both columns reappear, the marker is
        backfilled into the queue, and a subsequent ``Execution`` INSERT (the
        core execution-trigger path, not just status_error writes) succeeds.
        """
        import asyncio

        from sqlalchemy import text

        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine
        from governance_controller.models.execution import Execution
        from governance_controller.models.task import Task

        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        setup_engine = create_async_engine(url, echo=False, future=True)
        try:
            async with setup_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.drop_all)
                await conn.run_sync(SQLModel.metadata.create_all)
                # Simulate a pre-existing deployment: the column existed in
                # today's model but not in this (older) database.
                await conn.execute(
                    text("ALTER TABLE execution DROP COLUMN status_error")
                )
                await conn.execute(
                    text("ALTER TABLE execution DROP COLUMN cancellation_pending")
                )
                await conn.execute(
                    text(
                        "INSERT INTO execution "
                        "(id, task_id, state, started_at, macro_agent_run_id) "
                        "VALUES ('exec-cancel-migration', 'task-cancel-migration', "
                        "'FAILED', NOW(), 'run-cancel-migration')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO auditlog "
                        "(event_id, event_type, task_id, execution_id, actor, "
                        "source, timestamp, payload, previous_hash, row_hash) VALUES "
                        "('evt-cancel-migration', 'execution_cancel_pending', "
                        "'task-cancel-migration', 'exec-cancel-migration', "
                        "'system', 'test', NOW(), "
                        "'{\"macro_agent_run_id\": \"run-cancel-migration\"}', "
                        "'', '')"
                    )
                )
        finally:
            await setup_engine.dispose()

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        migration_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        migration_statements: list[str] = []

        def _capture_backfill(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            normalized = statement.lower()
            if (
                "update execution as e" in normalized
                or "execution_cancel_pending" in normalized
            ):
                migration_statements.append(statement)

        event.listen(
            migration_engine.sync_engine,
            "before_cursor_execute",
            _capture_backfill,
        )
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = migration_engine
            _engines_by_loop[asyncio.get_running_loop()] = migration_engine
            await run_migrations()
            first_run_backfills = list(migration_statements)

            # Idempotency: running it again with the column already present
            # must not error either.
            migration_statements.clear()
            await run_migrations()
            second_run_backfills = list(migration_statements)
        finally:
            event.remove(
                migration_engine.sync_engine,
                "before_cursor_execute",
                _capture_backfill,
            )
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await migration_engine.dispose()

        assert first_run_backfills
        assert second_run_backfills == []

        # The real regression check: a fresh Execution insert (the actual
        # production code path #268 was breaking) must succeed.
        verify_engine = create_async_engine(url, echo=False, future=True)
        try:
            async with AsyncSession(verify_engine) as session:
                task = Task(
                    id="task-status-error-migration",
                    project_id="proj-1",
                    proposed_by="agent-1",
                )
                session.add(task)
                await session.flush()
                execution = Execution(
                    id="exec-status-error-migration",
                    task_id=task.id,
                    state=TaskState.READY,
                    status_error="ConnectionRefusedError",
                )
                session.add(execution)
                await session.commit()

                result = await session.execute(
                    text(
                        "SELECT status_error FROM execution WHERE id = "
                        "'exec-status-error-migration'"
                    )
                )
                assert result.scalar_one() == "ConnectionRefusedError"

                pending = await session.execute(
                    text(
                        "SELECT cancellation_pending FROM execution WHERE id = "
                        "'exec-cancel-migration'"
                    )
                )
                assert pending.scalar_one() is True
        finally:
            await verify_engine.dispose()

    async def test_postgres_run_migrations_promotes_legacy_cancellation_payloads(
        self,
    ) -> None:
        """#308: recover payload-only markers and record malformed ones."""
        import asyncio
        from unittest.mock import AsyncMock

        from sqlalchemy import text

        from governance_controller.adapters.macro_agent.executor import (
            MacroAgentExecutor,
        )
        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine
        from governance_controller.models.execution import Execution
        from governance_controller.services.stuck_execution_poller import (
            StuckExecutionPoller,
        )

        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        setup_engine = create_async_engine(url, echo=False, future=True)
        try:
            async with setup_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.drop_all)
                await conn.run_sync(SQLModel.metadata.create_all)
                await conn.execute(
                    text("ALTER TABLE execution DROP COLUMN cancellation_pending")
                )
                await conn.execute(
                    text(
                        "INSERT INTO execution "
                        "(id, task_id, state, started_at, macro_agent_run_id) "
                        "VALUES ('exec-legacy-direct', 'task-legacy-direct', "
                        "'FAILED', NOW(), NULL), "
                        "('exec-legacy-task-run', 'task-legacy-task-run', "
                        "'FAILED', NOW(), 'run-legacy-task-run')"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO auditlog "
                        "(event_id, event_type, task_id, execution_id, actor, "
                        "source, timestamp, payload, previous_hash, row_hash) VALUES "
                        "('evt-legacy-direct', 'execution_cancel_pending', "
                        "'task-legacy-direct', 'exec-legacy-direct', 'system', "
                        "'legacy', NOW(), "
                        "'{\"macro_agent_run_id\": \"run-legacy-direct\"}', '', ''), "
                        "('evt-legacy-task-run', 'execution_cancel_pending', "
                        "'task-legacy-task-run', NULL, 'system', 'legacy', NOW(), "
                        "'{\"macro_agent_run_id\": \"run-legacy-task-run\"}', '', ''), "
                        "('evt-legacy-malformed', 'execution_cancel_pending', "
                        "'task-legacy-malformed', NULL, 'system', 'legacy', NOW(), "
                        "'{}', '', '')"
                    )
                )
        finally:
            await setup_engine.dispose()

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        migration_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = migration_engine
            _engines_by_loop[asyncio.get_running_loop()] = migration_engine
            await run_migrations()
        finally:
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await migration_engine.dispose()

        verify_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        try:
            async with AsyncSession(verify_engine, expire_on_commit=False) as session:
                direct = await session.scalar(
                    select(Execution).where(Execution.id == "exec-legacy-direct")
                )
                task_run = await session.scalar(
                    select(Execution).where(Execution.id == "exec-legacy-task-run")
                )
                assert direct is not None
                assert task_run is not None
                assert direct.macro_agent_run_id == "run-legacy-direct"
                assert direct.cancellation_pending is True
                assert task_run.cancellation_pending is True

                malformed = await session.execute(
                    select(AuditLog).where(
                        AuditLog.event_type == "execution_cancel_unrecoverable"
                    )
                )
                malformed_rows = malformed.scalars().all()
                assert len(malformed_rows) == 1
                assert malformed_rows[0].payload == {
                    "pending_event_id": "evt-legacy-malformed",
                    "reason": "no_matching_execution",
                }

                executor = AsyncMock(spec=MacroAgentExecutor)
                executor.cancel.return_value = {}
                actions = await StuckExecutionPoller(
                    session, executor=executor, batch_size=2
                )._poll_pending_cancellations()
                assert {
                    action["macro_agent_run_id"] for action in actions
                } == {"run-legacy-direct", "run-legacy-task-run"}
                assert executor.cancel.await_count == 2
        finally:
            await verify_engine.dispose()

    async def test_postgres_migration_orders_tip_lock_before_auditlog_ddl(
        self,
    ) -> None:
        """#308 round 2: first migration cannot cycle with an AuditLog writer."""
        import asyncio

        from sqlalchemy import text

        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine
        from governance_controller.models.audit_log import _AUDITLOG_TIP_LOCK_KEY

        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        setup_engine = create_async_engine(url, echo=False, future=True)
        try:
            async with setup_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.drop_all)
                await conn.run_sync(SQLModel.metadata.create_all)
                await conn.execute(
                    text(
                        "DROP TRIGGER IF EXISTS auditlog_block_update_delete "
                        "ON auditlog"
                    )
                )
                await conn.execute(
                    text("ALTER TABLE auditlog DROP COLUMN previous_hash")
                )
                await conn.execute(text("ALTER TABLE auditlog DROP COLUMN row_hash"))
                await conn.execute(
                    text("ALTER TABLE execution DROP COLUMN cancellation_pending")
                )
                await conn.execute(
                    text(
                        "INSERT INTO auditlog "
                        "(event_id, event_type, task_id, execution_id, actor, "
                        "source, timestamp, payload) VALUES "
                        "('evt-lock-order-malformed', 'execution_cancel_pending', "
                        "'task-lock-order-malformed', NULL, 'system', 'legacy', "
                        "NOW(), '{}')"
                    )
                )
        finally:
            await setup_engine.dispose()

        suffix = uuid4().hex[:8]
        migration_application = f"gc308-migration-{suffix}"
        writer_application = f"gc308-writer-{suffix}"
        migration_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={
                "server_settings": {"application_name": migration_application}
            },
        )
        writer_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"server_settings": {"application_name": writer_application}},
        )
        observer_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        migration_ddl_acquired = asyncio.Event()
        writer_ready = asyncio.Event()
        writer_action = asyncio.Event()
        writer_mode = "release_then_insert"
        writer_task: asyncio.Task[None] | None = None
        migration_task: asyncio.Task[None] | None = None
        ddl_wait_task: asyncio.Task[bool] | None = None
        advisory_wait_task: asyncio.Task[None] | None = None

        def _capture_migration_ddl(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            if statement.lower().lstrip().startswith(
                "alter table auditlog add column"
            ):
                migration_ddl_acquired.set()

        async def _writer() -> None:
            async with AsyncSession(writer_engine, expire_on_commit=False) as session:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _AUDITLOG_TIP_LOCK_KEY},
                )
                writer_ready.set()
                await writer_action.wait()
                if writer_mode == "insert_while_holding_lock":
                    await AuditService.log(
                        db=session,
                        event_type="concurrent_writer",
                        task_id="task-concurrent-writer",
                        actor="system",
                        source="test",
                    )
                else:
                    await session.commit()
                    await migration_ddl_acquired.wait()
                    await AuditService.log(
                        db=session,
                        event_type="concurrent_writer",
                        task_id="task-concurrent-writer",
                        actor="system",
                        source="test",
                    )
                await session.commit()

        async def _wait_for_migration_advisory_wait() -> None:
            async with observer_engine.connect() as observer:
                while True:
                    waiting = await observer.scalar(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_locks AS l
                                JOIN pg_stat_activity AS a ON a.pid = l.pid
                                WHERE a.application_name = :application_name
                                  AND l.locktype = 'advisory'
                                  AND NOT l.granted
                            )
                            """
                        ),
                        {"application_name": migration_application},
                    )
                    if waiting:
                        return
                    await asyncio.sleep(0.01)

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        db_module = __import__("governance_controller.db", fromlist=["engine"])
        event.listen(
            migration_engine.sync_engine,
            "after_cursor_execute",
            _capture_migration_ddl,
        )
        try:
            db_module.engine = migration_engine
            _engines_by_loop[asyncio.get_running_loop()] = migration_engine
            writer_task = asyncio.create_task(_writer())
            await asyncio.wait_for(writer_ready.wait(), timeout=5)
            migration_task = asyncio.create_task(run_migrations())
            ddl_wait_task = asyncio.create_task(migration_ddl_acquired.wait())
            advisory_wait_task = asyncio.create_task(
                _wait_for_migration_advisory_wait()
            )
            done, pending = await asyncio.wait(
                {ddl_wait_task, advisory_wait_task},
                timeout=5,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise AssertionError(
                    "migration did not reach DDL or advisory-lock wait"
                )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if migration_ddl_acquired.is_set():
                # The current implementation holds auditlog's DDL lock before
                # waiting on the writer's advisory lock. Make the writer enter
                # its before_insert path now, which closes the lock cycle.
                writer_mode = "insert_while_holding_lock"
            writer_action.set()
            await asyncio.wait_for(
                asyncio.gather(migration_task, writer_task), timeout=5
            )
            assert writer_mode == "release_then_insert"

            async with observer_engine.connect() as observer:
                writer_count = await observer.scalar(
                    text(
                        "SELECT count(*) FROM auditlog "
                        "WHERE event_type = 'concurrent_writer'"
                    )
                )
                assert writer_count == 1
        finally:
            for task in (
                ddl_wait_task,
                advisory_wait_task,
                migration_task,
                writer_task,
            ):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (
                        ddl_wait_task,
                        advisory_wait_task,
                        migration_task,
                        writer_task,
                    )
                    if task is not None
                ),
                return_exceptions=True,
            )
            event.remove(
                migration_engine.sync_engine,
                "after_cursor_execute",
                _capture_migration_ddl,
            )
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await observer_engine.dispose()
            await writer_engine.dispose()
            await migration_engine.dispose()

    async def test_postgres_migration_does_not_hold_tip_lock_during_execution_ddl(
        self,
    ) -> None:
        """#308 round 3: execution DDL cannot cycle with a poller audit write."""
        import asyncio

        from sqlalchemy import text

        from governance_controller.db import _engines_by_loop, run_migrations
        from governance_controller.db import engine as db_engine

        url = os.environ.get("GC_TEST_DATABASE_URL", "")
        setup_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        try:
            async with setup_engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.drop_all)
                await conn.run_sync(SQLModel.metadata.create_all)
                await conn.execute(
                    text("ALTER TABLE execution DROP COLUMN cancellation_pending")
                )
                await conn.execute(
                    text(
                        "INSERT INTO execution "
                        "(id, task_id, state, started_at, macro_agent_run_id) "
                        "VALUES ('exec-lock-order-row', 'task-lock-order-row', "
                        "'FAILED', NOW(), 'run-lock-order-row')"
                    )
                )
        finally:
            await setup_engine.dispose()

        suffix = uuid4().hex[:8]
        migration_application = f"gc308-migration-execution-{suffix}"
        poller_application = f"gc308-poller-execution-{suffix}"
        migration_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={
                "server_settings": {"application_name": migration_application}
            },
        )
        poller_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={
                "server_settings": {"application_name": poller_application}
            },
        )
        observer_engine = create_async_engine(
            url, echo=False, future=True, poolclass=NullPool
        )
        poller_ready = asyncio.Event()
        start_audit_write = asyncio.Event()
        audit_write_flushed = asyncio.Event()
        release_execution_lock = asyncio.Event()
        poller_task: asyncio.Task[None] | None = None
        migration_task: asyncio.Task[None] | None = None

        async def _poller_transaction() -> None:
            async with AsyncSession(
                poller_engine, expire_on_commit=False
            ) as session:
                await session.execute(
                    text(
                        "SELECT id FROM execution "
                        "WHERE id = 'exec-lock-order-row' FOR UPDATE"
                    )
                )
                poller_ready.set()
                await start_audit_write.wait()
                await AuditService.log(
                    db=session,
                    event_type="execution_lock_order_writer",
                    task_id="task-lock-order-row",
                    actor="system",
                    source="stuck_execution_poller",
                    execution_id="exec-lock-order-row",
                )
                audit_write_flushed.set()
                await release_execution_lock.wait()
                await session.commit()

        async def _wait_for_execution_ddl_wait() -> None:
            async with observer_engine.connect() as observer:
                while True:
                    waiting = await observer.scalar(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_locks AS l
                                JOIN pg_stat_activity AS a ON a.pid = l.pid
                                WHERE a.application_name = :application_name
                                  AND l.locktype = 'relation'
                                  AND l.relation = 'execution'::regclass
                                  AND l.mode = 'AccessExclusiveLock'
                                  AND NOT l.granted
                            )
                            """
                        ),
                        {"application_name": migration_application},
                    )
                    if waiting:
                        return
                    await asyncio.sleep(0.01)

        original_engine = db_engine
        original_engines = dict(_engines_by_loop)
        db_module = __import__("governance_controller.db", fromlist=["engine"])
        try:
            db_module.engine = migration_engine
            _engines_by_loop[asyncio.get_running_loop()] = migration_engine
            poller_task = asyncio.create_task(_poller_transaction())
            await asyncio.wait_for(poller_ready.wait(), timeout=5)

            migration_task = asyncio.create_task(run_migrations())
            await asyncio.wait_for(_wait_for_execution_ddl_wait(), timeout=5)

            start_audit_write.set()
            await asyncio.wait_for(audit_write_flushed.wait(), timeout=5)
            release_execution_lock.set()
            await asyncio.wait_for(
                asyncio.gather(migration_task, poller_task), timeout=5
            )

            async with observer_engine.connect() as observer:
                writer_count = await observer.scalar(
                    text(
                        "SELECT count(*) FROM auditlog "
                        "WHERE event_type = 'execution_lock_order_writer'"
                    )
                )
                assert writer_count == 1
        finally:
            release_execution_lock.set()
            for task in (migration_task, poller_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (migration_task, poller_task) if task is not None),
                return_exceptions=True,
            )
            db_module.engine = original_engine
            _engines_by_loop.clear()
            _engines_by_loop.update(original_engines)
            await observer_engine.dispose()
            await poller_engine.dispose()
            await migration_engine.dispose()

    async def test_postgres_trigger_blocks_update_and_delete(
        self, isolated_db: tuple[AsyncEngine, sessionmaker]
    ) -> None:
        """#126: trigger created by Postgres DDL blocks Core UPDATE/DELETE."""
        engine, session_local = isolated_db
        assert engine.dialect.name == "postgresql"
        async with session_local() as session:
            from sqlalchemy import delete, update

            await AuditService.log(
                db=session,
                event_type="approval",
                task_id="task-pg-trigger",
                actor="human-1",
                source="plane",
            )

            with pytest.raises(Exception, match="append-only"):
                await session.execute(
                    update(AuditLog)
                    .where(AuditLog.task_id == "task-pg-trigger")
                    .values(actor="tampered")
                )
                await session.commit()

        async with session_local() as session:
            from sqlalchemy import delete

            await AuditService.log(
                db=session,
                event_type="approval",
                task_id="task-pg-trigger-del",
                actor="human-1",
                source="plane",
            )

            with pytest.raises(Exception, match="append-only"):
                await session.execute(
                    delete(AuditLog).where(AuditLog.task_id == "task-pg-trigger-del")
                )
                await session.commit()

    async def test_concurrent_writes_form_one_hash_chain(
        self, isolated_db: tuple[AsyncEngine, sessionmaker]
    ) -> None:
        """#280: genuine concurrent Postgres writers must not fork the chain."""
        import asyncio

        _engine, session_local = isolated_db
        async with session_local() as session:
            await AuditService.log(
                db=session,
                event_type="seed",
                task_id="task-concurrent-hash",
                actor="system",
                source="test",
            )
            await session.commit()

        writers = 12
        barrier = asyncio.Barrier(writers)

        async def write(index: int) -> None:
            async with session_local() as session:
                await barrier.wait()
                await AuditService.log(
                    db=session,
                    event_type="concurrent",
                    task_id="task-concurrent-hash",
                    actor=f"writer-{index}",
                    source="test",
                    payload={"index": index},
                )
                await session.commit()

        await asyncio.gather(*(write(index) for index in range(writers)))

        async with session_local() as session:
            rows = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.task_id == "task-concurrent-hash")
                    .order_by(AuditLog.id)
                )
            ).scalars().all()

        assert len(rows) == writers + 1
        assert len({row.previous_hash for row in rows[1:]}) == writers
        for previous, current in zip(rows, rows[1:], strict=False):
            assert current.previous_hash == previous.row_hash

    async def test_audit_lock_is_released_before_slow_projection(
        self, isolated_db: tuple[AsyncEngine, sessionmaker]
    ) -> None:
        """#283: projection I/O must not hold the global audit tip lock."""
        engine, session_local = isolated_db
        assert engine.dialect.name == "postgresql"

        class _SlowProjection:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def update_state(self, **_kwargs: Any) -> dict[str, Any]:
                self.started.set()
                await self.release.wait()
                return {}

        async with session_local() as seed:
            task = await _make_task(seed, task_id="task-slow-projection")
            await seed.commit()

        slow_projection = _SlowProjection()
        slow_session = session_local()
        try:
            task = await slow_session.scalar(
                select(Task).where(Task.id == "task-slow-projection")
            )
            assert task is not None
            service = ApprovalService(
                db=slow_session,
                plane_projection=slow_projection,  # type: ignore[arg-type]
            )
            await AuditService.log(
                db=slow_session,
                event_type="state_change",
                task_id=task.id,
                actor="system",
                source="test",
            )

            projection_task = asyncio.create_task(
                service._project_state_to_plane(
                    task=task,
                    state=TaskState.PLAN_APPROVED,
                    approval_type=ApprovalType.PLAN,
                )
            )
            await slow_projection.started.wait()

            async def write_unrelated_audit() -> None:
                async with session_local() as unrelated:
                    await AuditService.log(
                        db=unrelated,
                        event_type="unrelated_write",
                        task_id="task-other",
                        actor="system",
                        source="test",
                    )
                    await unrelated.commit()

            await asyncio.wait_for(write_unrelated_audit(), timeout=0.5)
            slow_projection.release.set()
            await projection_task
        finally:
            if not slow_projection.release.is_set():
                slow_projection.release.set()
            if "projection_task" in locals():
                await projection_task
            await slow_session.rollback()


class TestAuditServiceSideEffects:
    async def test_structlog_event_is_emitted(
        self,
        db_session: AsyncSession,
    ) -> None:
        with patch(
            "governance_controller.services.audit_service.logger.info"
        ) as mock_info:
            await AuditService.log(
                db=db_session,
                event_type="approval",
                task_id="task-1",
                actor="human-1",
                source="plane",
                payload={"foo": "bar"},
            )

        mock_info.assert_called_once()
        call_kwargs = mock_info.call_args.kwargs
        assert call_kwargs["event_type"] == "approval"
        assert call_kwargs["task_id"] == "task-1"
        assert call_kwargs["actor"] == "human-1"
        assert call_kwargs["source"] == "plane"


class TestApprovalServiceAuditIntegration:
    async def test_approval_service_writes_audit_entries(
        self, db_session: AsyncSession
    ) -> None:
        service = ApprovalService(db=db_session)
        task = await _make_task(db_session, TaskState.PROPOSED)
        contract = _make_contract()
        profile = _make_profile()

        await service.approve(
            task=task,
            contract=contract,
            profile=profile,
            approval_type=ApprovalType.PLAN,
            source="plane",
            actor="human-1",
            idempotency_key="key-plan-1",
        )

        rows = await db_session.execute(
            select(AuditLog).where(AuditLog.task_id == task.id)
        )
        entries = rows.scalars().all()

        assert len(entries) >= 2

        approval_entries = [e for e in entries if e.event_type == "approval"]
        state_change_entries = [e for e in entries if e.event_type == "state_change"]

        assert approval_entries
        assert state_change_entries

        approval = approval_entries[0]
        assert approval.actor == "human-1"
        assert approval.source == "plane"
        assert approval.payload.get("new_state") == TaskState.PLAN_APPROVED.value

        change = state_change_entries[0]
        assert change.payload.get("previous_state") == TaskState.PROPOSED.value
        assert change.payload.get("new_state") == TaskState.PLAN_APPROVED.value
