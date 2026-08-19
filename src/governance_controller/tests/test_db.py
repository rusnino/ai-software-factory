import os
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import DateTime, text

from governance_controller.db import get_db
from governance_controller.models.approval import Approval
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.execution import Execution
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task

_TIME_COLUMNS = {
    Task: {"created_at", "updated_at"},
    Approval: {"timestamp"},
    AuditLog: {"timestamp"},
    Execution: {"started_at", "ended_at"},
    ProcessedEvent: {"event_timestamp", "processed_at"},
}


@pytest.mark.parametrize(
    "model, columns", [(m, _TIME_COLUMNS[m]) for m in _TIME_COLUMNS]
)
def test_datetime_columns_use_timezone(model, columns):
    for col_name in columns:
        col = model.__table__.columns[col_name]
        assert isinstance(col.type, DateTime) and col.type.timezone is True


@pytest.mark.asyncio
async def test_init_db_and_session(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_get_db_yields_session(patched_db):
    async with asynccontextmanager(get_db)() as session:
        assert session is not None


async def test_get_db_commits(patched_db):
    async with asynccontextmanager(get_db)() as session:
        task = Task(id="commit-1", project_id="p1", proposed_by="tester")
        session.add(task)

    async with asynccontextmanager(get_db)() as session:
        result = await session.get(Task, "commit-1")
        assert result is not None


async def test_get_db_rolls_back_on_exception(patched_db):
    with pytest.raises(ValueError, match="boom"):
        async with asynccontextmanager(get_db)() as session:
            task = Task(id="rollback-exc", project_id="p1", proposed_by="tester")
            session.add(task)
            raise ValueError("boom")

    async with asynccontextmanager(get_db)() as session:
        assert await session.get(Task, "rollback-exc") is None


async def test_get_db_rolls_back_on_generator_exit(patched_db):
    async for session in get_db():
        task = Task(id="rollback-gen", project_id="p1", proposed_by="tester")
        session.add(task)
        break

    async with asynccontextmanager(get_db)() as session:
        assert await session.get(Task, "rollback-gen") is None


async def test_get_db_commit_branch_requires_exception_catch(patched_db):
    """Mutation test: if except Exception were used, an exception thrown INTO
    the generator (as FastAPI does) would *not* be caught and rollback would not
    run, leaving the uncommitted insert visible to a later session. The
    BaseException branch ensures rollback happens and the insert is absent.
    """
    gen = get_db()
    session = await gen.asend(None)
    task = Task(id="thrown-exc", project_id="p1", proposed_by="tester")
    session.add(task)

    with pytest.raises(ValueError, match="thrown"):
        await gen.athrow(ValueError("thrown"))

    async with asynccontextmanager(get_db)() as session:
        assert await session.get(Task, "thrown-exc") is None


async def test_get_db_success_branch_requires_no_exception(patched_db):
    """Confirm that the commit branch is exercised on normal exit."""
    from contextlib import suppress

    gen = get_db()
    session = await gen.asend(None)
    task = Task(id="thrown-commit", project_id="p1", proposed_by="tester")
    session.add(task)

    with suppress(StopAsyncIteration):
        await gen.asend(None)

    async with asynccontextmanager(get_db)() as session:
        assert await session.get(Task, "thrown-commit") is not None


def test_file_based_sqlite_uses_tuned_pool(tmp_path, monkeypatch):
    """File-based SQLite should receive pool_size/max_overflow/pool_timeout."""
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("GC_DATABASE_URL", str(db_url))
    monkeypatch.setenv("GC_DATABASE_POOL_SIZE", "3")
    monkeypatch.setenv("GC_DATABASE_MAX_OVERFLOW", "7")
    monkeypatch.setenv("GC_DATABASE_POOL_TIMEOUT", "5")

    import importlib

    from governance_controller import config
    from governance_controller import db as db_module

    with patch.dict(os.environ, {"GC_DATABASE_URL": str(db_url)}):
        importlib.reload(config)
        importlib.reload(db_module)

    assert db_module.engine.url.database == str(db_file)
    assert db_module.engine.pool.size() == 3
    assert db_module.engine.pool.timeout() == 5
