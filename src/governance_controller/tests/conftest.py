import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

_TEST_DB_URL_ENV = "GC_TEST_DATABASE_URL"
_TEST_DB_URL_DEFAULT = "sqlite+aiosqlite:///:memory:"


def _test_database_url() -> str:
    return os.environ.get(_TEST_DB_URL_ENV, _TEST_DB_URL_DEFAULT)


@pytest.fixture
def test_database_url() -> str:
    return _test_database_url()


async def _create_tables(test_db_url: str, test_engine) -> None:
    """Create all tables on SQLite or PostgreSQL."""
    if test_db_url.startswith("sqlite"):
        async with test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
    else:
        # Postgres: drop and recreate so each test process starts clean.
        async with test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    test_db_url = _test_database_url()
    test_engine = create_async_engine(test_db_url, echo=False, future=True)
    test_session_local = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    await _create_tables(test_db_url, test_engine)

    async with test_session_local() as session:
        # Provide a session without an active begin() context so that code
        # that commits (e.g. rejection audit logging) does not close a
        # transactional context and break subsequent fixture operations.
        yield session
        # Rollback for tests that did not commit; committed tests leave the
        # transaction closed, so rollback becomes a no-op.
        await session.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncSession]:
    """Provide a session for API tests, backed by the configured test DB.

    By default this is a file-backed SQLite database so httpx's ASGI transport
    sees the same data across requests. Set ``GC_TEST_DATABASE_URL`` to run API
    tests against PostgreSQL instead.
    """
    test_db_url = _test_database_url()
    if test_db_url.startswith("sqlite"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            test_db_url = f"sqlite+aiosqlite:///{tmp.name}"

    test_engine = create_async_engine(test_db_url, echo=False, future=True)
    test_session_local = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    await _create_tables(test_db_url, test_engine)

    async with test_session_local() as session:
        from governance_controller.db import settings

        def _make_override():
            async def _override_get_db() -> AsyncGenerator[AsyncSession]:
                yield session

            return _override_get_db

        # Patch the module-level engine/sessionmaker too, so code that touches
        # default_engine (e.g. get_db, EventBridge helpers) uses the test DB.
        monkeypatch.setattr(
            "governance_controller.db.get_db", _make_override()
        )
        monkeypatch.setattr("governance_controller.db.engine", test_engine)
        monkeypatch.setattr(
            "governance_controller.db.AsyncSessionLocal", test_session_local
        )
        monkeypatch.setattr(settings, "database_url", test_db_url)

        # Provide a session without an active begin() context so that code
        # that commits (e.g. rejection audit logging) does not close a
        # transactional context and break subsequent fixture operations.
        yield session
        # Rollback for tests that did not commit; committed tests leave the
        # transaction closed, so rollback becomes a no-op.
        await session.rollback()

    await test_engine.dispose()
    if test_db_url != _test_database_url() and test_db_url.startswith("sqlite"):
        os.unlink(test_db_url.replace("sqlite+aiosqlite:///", ""))


