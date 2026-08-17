import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from governance_controller.db import AsyncSessionLocal, init_db
from governance_controller.db import engine as default_engine


async def _postgres_available() -> bool:
    try:
        async with default_engine.connect() as _:
            return True
    except (OperationalError, OSError):
        return False


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    if not await _postgres_available():
        test_db_url = os.environ.get(
            "GC_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
        )
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
        return

    await init_db()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session
        await session.rollback()

