import pytest
from sqlalchemy import text

from governance_controller.db import get_db


@pytest.mark.asyncio
async def test_init_db_and_session(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_get_db_yields_session():
    sessions = []
    async for session in get_db():
        sessions.append(session)
        break
    assert len(sessions) == 1
