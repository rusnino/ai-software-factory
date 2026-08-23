"""Tests for the AuditLog model and AuditService."""

import os
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import select
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
    task = Task(
        id=task_id, project_id="proj-1", state=state, proposed_by="agent-1"
    )
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

    async def test_hash_chain_links_rows(
        self, db_session: AsyncSession
    ) -> None:
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
            await db_session.execute(
                select(AuditLog).where(AuditLog.id == second.id)
            )
        ).scalar_one()
        assert reloaded.row_hash == reloaded.compute_hash()

    async def test_audit_row_update_is_blocked(
        self, db_session: AsyncSession
    ) -> None:
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

    async def test_audit_row_delete_is_blocked(
        self, db_session: AsyncSession
    ) -> None:
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

    async def test_core_bulk_update_is_blocked(
        self, db_session: AsyncSession
    ) -> None:
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
                update(AuditLog).where(
                    AuditLog.task_id == "task-core-update"
                ).values(actor="tampered")
            )
            await db_session.commit()

    async def test_core_bulk_delete_is_blocked(
        self, db_session: AsyncSession
    ) -> None:
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
                delete(AuditLog).where(
                    AuditLog.task_id == "task-core-delete"
                )
            )
            await db_session.commit()


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


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
        from governance_controller.db import engine as db_engine
        from governance_controller.db import run_migrations

        original_engine = db_engine
        test_engine = create_async_engine(url, echo=False, future=True)
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = test_engine
            await run_migrations()
        finally:
            db_module.engine = original_engine
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
        from governance_controller.db import engine as db_engine
        from governance_controller.db import run_migrations

        original_engine = db_engine
        migration_engine = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
        )
        try:
            db_module = __import__("governance_controller.db", fromlist=["engine"])
            db_module.engine = migration_engine
            await run_migrations()
        finally:
            db_module.engine = original_engine
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
                    delete(AuditLog).where(
                        AuditLog.task_id == "task-pg-trigger-del"
                    )
                )
                await session.commit()


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
