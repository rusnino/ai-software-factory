import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from governance_controller.db import (
    AsyncSessionLocal,
    init_db,
)
from governance_controller.db import (
    engine as default_engine,
)

_TEST_DB_URL_ENV = "GC_TEST_DATABASE_URL"
_TEST_DB_URL_DEFAULT = "sqlite+aiosqlite:///:memory:"


def _test_database_url() -> str:
    return os.environ.get(_TEST_DB_URL_ENV, _TEST_DB_URL_DEFAULT)


async def _postgres_available() -> bool:
    try:
        async with default_engine.connect() as _:
            return True
    except (OperationalError, OSError):
        return False


@pytest.fixture
def test_database_url() -> str:
    return _test_database_url()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    if await _postgres_available():
        await init_db()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                yield session
            await session.rollback()
        return

    test_db_url = _test_database_url()
    test_engine = create_async_engine(test_db_url, echo=False, future=True)
    test_session_local = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with test_session_local() as session:
        async with session.begin():
            yield session
        await session.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client_db_session(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncSession]:
    """Provide a file-backed SQLite session for API tests.

    A file database is used instead of :memory: because httpx's ASGI transport
    runs each request in a way that can open a separate aiosqlite connection.
    With a shared file, all requests in the same test see the same data.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        test_db_url = f"sqlite+aiosqlite:///{tmp.name}"

    test_engine = create_async_engine(test_db_url, echo=False, future=True)
    test_session_local = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with test_session_local() as session:

        def _make_override():
            async def _override_get_db() -> AsyncGenerator[AsyncSession]:
                yield session

            return _override_get_db

        monkeypatch.setattr(
            "governance_controller.db.get_db", _make_override()
        )
        async with session.begin():
            yield session
        await session.rollback()

    await test_engine.dispose()
    os.unlink(tmp.name)

