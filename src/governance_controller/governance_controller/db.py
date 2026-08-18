from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from governance_controller.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout,
    pool_pre_ping=settings.database_pool_pre_ping,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def ensure_sqlite_tables() -> None:
    """Create all tables for in-memory SQLite on the async connection."""
    if not settings.database_url.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession]:
    if settings.database_url.startswith("sqlite"):
        await ensure_sqlite_tables()
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
