import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

_TEST_DB_URL_ENV = "GC_TEST_DATABASE_URL"
_TEST_DB_URL_DEFAULT = "sqlite+aiosqlite:///:memory:"


def _test_database_url() -> str:
    return os.environ.get(_TEST_DB_URL_ENV, _TEST_DB_URL_DEFAULT)


@pytest.fixture
def test_database_url() -> str:
    return _test_database_url()


@pytest.fixture(autouse=True)
def _test_admin_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a non-empty admin list for tests that exercise EXECUTION/MERGE approvals.

    Production defaults to an empty list (fail-closed); tests opt into a test
    admin so they can verify approval state transitions without hardcoding the
    same skeleton key.
    """
    monkeypatch.setattr(
        "governance_controller.config.settings.admins", "admin"
    )


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


async def _create_tables(test_db_url: str, test_engine: AsyncEngine) -> None:
    """Create all tables on SQLite or PostgreSQL."""
    if _is_sqlite(test_db_url):
        async with test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    else:
        # Postgres: drop and recreate so each test process starts clean.
        async with test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)


async def _make_engine_and_session(
    test_db_url: str,
) -> tuple[AsyncEngine, sessionmaker]:
    """Return a fresh async engine and sessionmaker for ``test_db_url``."""
    test_engine = create_async_engine(test_db_url, echo=False, future=True)
    test_session_local = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return test_engine, test_session_local


@pytest_asyncio.fixture
async def isolated_db() -> AsyncGenerator[tuple[AsyncEngine, sessionmaker]]:
    """Provide a clean, isolated database engine + sessionmaker.

    When ``GC_TEST_DATABASE_URL`` points at PostgreSQL, the same database is
    reused across tests in the same process, with tables dropped/recreated for
    isolation. When using SQLite (the default), a new in-memory or temporary
    file database is created per test.
    """
    test_db_url = _test_database_url()

    if _is_sqlite(test_db_url):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            test_db_url = f"sqlite+aiosqlite:///{tmp.name}"
        cleanup_path = tmp.name
    else:
        cleanup_path = None

    test_engine, test_session_local = await _make_engine_and_session(test_db_url)
    await _create_tables(test_db_url, test_engine)

    yield test_engine, test_session_local

    await test_engine.dispose()
    if cleanup_path:
        os.unlink(cleanup_path)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    test_db_url = _test_database_url()
    test_engine, test_session_local = await _make_engine_and_session(test_db_url)
    await _create_tables(test_db_url, test_engine)

    async with test_session_local() as session:
        yield session
        await session.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncSession]:
    """Provide a session for API tests, backed by the configured test DB."""
    test_db_url = _test_database_url()
    test_engine, test_session_local = await _make_engine_and_session(test_db_url)
    await _create_tables(test_db_url, test_engine)

    async with test_session_local() as session:

        def _make_override():
            async def _override_get_db() -> AsyncGenerator[AsyncSession]:
                yield session

            return _override_get_db

        # Patch the module-level engine/sessionmaker too, so code that touches
        # default_engine (e.g. get_db, EventBridge helpers) uses the test DB.
        monkeypatch.setattr("governance_controller.db.get_db", _make_override())
        monkeypatch.setattr("governance_controller.db.engine", test_engine)
        monkeypatch.setattr(
            "governance_controller.db.AsyncSessionLocal", test_session_local
        )
        monkeypatch.setattr(
            "governance_controller.config.settings.database_url", test_db_url
        )

        yield session
        await session.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture
async def patched_db(isolated_db: tuple[AsyncEngine, sessionmaker]):
    """Patch production ``db`` module globals to the isolated test database."""
    test_engine, test_session_local = isolated_db
    test_db_url = test_engine.url.render_as_string(hide_password=False)

    from governance_controller import db as db_module

    original_engine = db_module.engine
    original_session_local = db_module.AsyncSessionLocal
    original_database_url = db_module.settings.database_url

    db_module.engine = test_engine
    db_module.AsyncSessionLocal = test_session_local
    db_module.settings.database_url = test_db_url

    yield test_engine, test_session_local

    db_module.engine = original_engine
    db_module.AsyncSessionLocal = original_session_local
    db_module.settings.database_url = original_database_url
