from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import AsyncSessionLocal, engine, init_db


async def _postgres_available() -> bool:
    try:
        async with engine.connect() as _:
            return True
    except (OperationalError, OSError):
        return False


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    if not await _postgres_available():
        pytest.skip("PostgreSQL is not available")

    await init_db()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session
        await session.rollback()

