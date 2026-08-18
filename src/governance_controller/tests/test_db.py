from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import governance_controller.db as db_module
from governance_controller.db import get_db
from governance_controller.models.task import Task


@pytest.mark.asyncio
async def test_init_db_and_session(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest_asyncio.fixture
async def in_memory_get_db(monkeypatch):
    """Configure ``get_db`` with a fresh in-memory SQLite database."""
    database_url = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(database_url, echo=False, future=True)
    test_session_local = db_module.async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
    )

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", test_session_local)
    monkeypatch.setattr(db_module.settings, "database_url", database_url)

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield get_db

    await test_engine.dispose()


async def test_get_db_yields_session(in_memory_get_db):
    async with asynccontextmanager(in_memory_get_db)() as session:
        assert session is not None


async def test_get_db_commits(in_memory_get_db):
    async with asynccontextmanager(in_memory_get_db)() as session:
        task = Task(id="commit-1", project_id="p1", proposed_by="tester")
        session.add(task)

    async with asynccontextmanager(in_memory_get_db)() as session:
        result = await session.get(Task, "commit-1")
        assert result is not None


async def test_get_db_rolls_back_on_exception(in_memory_get_db):
    with pytest.raises(ValueError, match="boom"):
        async with asynccontextmanager(in_memory_get_db)() as session:
            task = Task(id="rollback-exc", project_id="p1", proposed_by="tester")
            session.add(task)
            raise ValueError("boom")

    async with asynccontextmanager(in_memory_get_db)() as session:
        assert await session.get(Task, "rollback-exc") is None


async def test_get_db_rolls_back_on_generator_exit(in_memory_get_db):
    async for session in in_memory_get_db():
        task = Task(id="rollback-gen", project_id="p1", proposed_by="tester")
        session.add(task)
        break

    async with asynccontextmanager(in_memory_get_db)() as session:
        assert await session.get(Task, "rollback-gen") is None
