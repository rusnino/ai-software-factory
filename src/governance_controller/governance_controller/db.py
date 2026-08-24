from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from governance_controller.config import settings


def _is_in_memory_sqlite(database_url: str) -> bool:
    """Return True for in-memory SQLite URLs that require StaticPool."""
    return database_url.startswith("sqlite") and ":memory:" in database_url


_ENGINE_KWARGS: dict[str, object] = {
    "echo": False,
    "future": True,
    "pool_pre_ping": settings.database_pool_pre_ping,
}

if not _is_in_memory_sqlite(settings.database_url):
    _ENGINE_KWARGS.update(
        {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_timeout": settings.database_pool_timeout,
        }
    )

# Allow tests to override the engine so migration/helper functions can be
# exercised against an isolated database.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **_ENGINE_KWARGS,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


def _get_audit_log_table() -> type[SQLModel]:
    from governance_controller.models.audit_log import AuditLog

    return AuditLog


async def run_migrations() -> None:
    """Apply lightweight startup migrations that ``create_all`` skips.

    ``SQLModel.metadata.create_all`` does not alter existing tables, so adding
    new columns (such as AuditLog's ``previous_hash``/``row_hash``) to a
    pre-existing database requires an explicit ``ALTER TABLE``. This function
    is a minimal in-code migration runner for Phase 1; a full Alembic setup
    may replace it in Phase 2.

    This migration path is designed for the production Postgres database.
    SQLite dev/test deployments only run ``create_all()`` via
    ``ensure_sqlite_tables()``, so legacy SQLite auditlog tables do not receive
    the ``previous_hash``/``row_hash`` backfill or immutability triggers.
    """
    # Force the AuditLog model to be imported so its table name is known.
    _get_audit_log_table()
    async with engine.begin() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        if "auditlog" not in tables:
            # The AuditLog table does not exist yet; create_all() has not run
            # and there is nothing to migrate. Raising a clear error prevents a
            # confusing NoSuchTableError from SQLAlchemy introspection.
            raise RuntimeError(
                "run_migrations() called before auditlog table exists; "
                "run create_all() first"
            )

        columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("auditlog")
        )
        column_names = {c["name"] for c in columns}
        dialect_name = conn.dialect.name

        new_columns: list[tuple[str, str]] = []
        if "previous_hash" not in column_names:
            new_columns.append(("previous_hash", "VARCHAR"))
        if "row_hash" not in column_names:
            new_columns.append(("row_hash", "VARCHAR"))

        for column_name, column_type in new_columns:
            await conn.execute(
                text(
                    f"ALTER TABLE auditlog ADD COLUMN {column_name} "
                    f"{column_type} DEFAULT ''"
                )
            )

        # Backfill any legacy rows that were inserted before the migration.
        # Hash values cannot be reconstructed deterministically for old rows,
        # but empty-string placeholders let the chain continue from this point.
        if new_columns:
            await conn.execute(
                text(
                    "UPDATE auditlog SET previous_hash = '' "
                    "WHERE previous_hash IS NULL OR previous_hash = ''"
                )
            )
            await conn.execute(
                text(
                    "UPDATE auditlog SET row_hash = '' "
                    "WHERE row_hash IS NULL OR row_hash = ''"
                )
            )

        # Apply DB-level immutability triggers to existing tables (#120).
        from governance_controller.models.audit_log import (
            _AUDITLOG_POSTGRES_CREATE_TRIGGER,
            _AUDITLOG_POSTGRES_DROP_TRIGGER,
            _AUDITLOG_POSTGRES_FUNCTION,
            _AUDITLOG_SQLITE_DELETE_TRIGGER,
            _AUDITLOG_SQLITE_DROP_DELETE,
            _AUDITLOG_SQLITE_DROP_UPDATE,
            _AUDITLOG_SQLITE_UPDATE_TRIGGER,
        )

        if dialect_name == "postgresql":
            await conn.execute(_AUDITLOG_POSTGRES_FUNCTION)
            await conn.execute(_AUDITLOG_POSTGRES_DROP_TRIGGER)
            await conn.execute(_AUDITLOG_POSTGRES_CREATE_TRIGGER)
        elif dialect_name == "sqlite":
            await conn.execute(_AUDITLOG_SQLITE_DROP_UPDATE)
            await conn.execute(_AUDITLOG_SQLITE_DROP_DELETE)
            await conn.execute(_AUDITLOG_SQLITE_UPDATE_TRIGGER)
            await conn.execute(_AUDITLOG_SQLITE_DELETE_TRIGGER)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await run_migrations()


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


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Provide a standalone async DB session for non-FastAPI callers.

    Yields a session inside an ``async with`` block so that connections are
    returned to the pool even when callers forget to close the session. This
    prevents connection leaks/crashes on repeated in-process invocation.
    """
    if settings.database_url.startswith("sqlite"):
        await ensure_sqlite_tables()
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
