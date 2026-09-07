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


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
@pytest.mark.asyncio
async def test_concurrent_postgres_init_db_processes_create_schema_once(
    test_database_url: str,
) -> None:
    """#328: fresh concurrent startup must not race PostgreSQL schema creation."""
    import asyncio
    import subprocess
    import sys

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    setup_engine = create_async_engine(
        test_database_url, echo=False, future=True, poolclass=NullPool
    )
    try:
        async with setup_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            for enum_name in ("approvaltype", "taskstate"):
                await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
    finally:
        await setup_engine.dispose()

    child_code = (
        "import asyncio; "
        "import governance_controller.models; "
        "from governance_controller.db import init_db; "
        "asyncio.run(init_db())"
    )
    child_environment = os.environ.copy()
    child_environment["GC_DATABASE_URL"] = test_database_url
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]

    try:
        results = await asyncio.gather(
            *(asyncio.to_thread(process.communicate) for process in processes)
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        await asyncio.gather(
            *(
                asyncio.to_thread(process.wait)
                for process in processes
                if process.poll() is None
            )
        )

    failures = [
        f"process {index} exited {process.returncode}:\n{stderr}"
        for index, (process, (_stdout, stderr)) in enumerate(
            zip(processes, results, strict=True), 1
        )
        if process.returncode != 0
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.skipif(
    not os.environ.get("GC_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL",
)
@pytest.mark.asyncio
async def test_eight_postgres_startups_share_init_db_migration_lock(
    test_database_url: str,
) -> None:
    """#328: eight real startup processes must all complete on a fresh database."""
    import asyncio
    import subprocess
    import sys

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    setup_engine = create_async_engine(
        test_database_url, echo=False, future=True, poolclass=NullPool
    )
    try:
        async with setup_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            for enum_name in ("approvaltype", "taskstate"):
                await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
    finally:
        await setup_engine.dispose()

    child_code = (
        "import asyncio; "
        "import governance_controller.models; "
        "from governance_controller.db import init_db; "
        "asyncio.run(init_db())"
    )
    child_environment = os.environ.copy()
    child_environment["GC_DATABASE_URL"] = test_database_url
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]

    try:
        results = await asyncio.gather(
            *(asyncio.to_thread(process.communicate) for process in processes)
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        await asyncio.gather(
            *(
                asyncio.to_thread(process.wait)
                for process in processes
                if process.poll() is None
            )
        )

    failures = [
        f"process {index} exited {process.returncode}:\n{stderr}"
        for index, (process, (_stdout, stderr)) in enumerate(
            zip(processes, results, strict=True), 1
        )
        if process.returncode != 0
    ]
    assert not failures, "\n".join(failures)


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

    # Preserve module-level objects that other tests rely on via import.
    original_engine = db_module.engine
    original_session_local = db_module.AsyncSessionLocal
    original_get_db = db_module.get_db
    original_config_settings = config.settings

    try:
        with patch.dict(os.environ, {"GC_DATABASE_URL": str(db_url)}):
            importlib.reload(config)
            importlib.reload(db_module)

        assert db_module.engine.url.database == str(db_file)
        assert db_module.engine.pool.size() == 3
        assert db_module.engine.pool.timeout() == 5
    finally:
        # Restore so subsequent tests that patch db_module attributes see the
        # original module objects again.
        db_module.engine = original_engine
        db_module.AsyncSessionLocal = original_session_local
        db_module.get_db = original_get_db
        config.settings = original_config_settings
