import asyncio
import weakref
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, inspect, text
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

def _make_engine(url: str | None = None) -> AsyncEngine:
    """Create a fresh async engine for *url* with project settings."""
    database_url = url or settings.database_url
    kwargs: dict[str, object] = {
        "echo": False,
        "future": True,
        "pool_pre_ping": settings.database_pool_pre_ping,
    }
    if not _is_in_memory_sqlite(database_url):
        kwargs.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout,
            }
        )
    return create_async_engine(database_url, **kwargs)


# Module-level engine kept for backwards compatibility with code and tests that
# import it directly. Internal helpers use get_engine() so repeated asyncio.run()
# calls in the same process do not recycle connections bound to a closed loop.
engine: AsyncEngine = _make_engine()

# Per-event-loop engine cache. WeakKeyDictionary lets engines be garbage
# collected once their loop is gone, which is enough for CLI/test lifecycles.
_engines_by_loop: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    AsyncEngine,
] = weakref.WeakKeyDictionary()


def get_engine() -> AsyncEngine:
    """Return an engine bound to the current event loop.

    Creates and caches a new engine when called from a loop that has not been
    seen before. This prevents ``RuntimeError: attached to a different loop``
    when the module is imported in one asyncio.run() context and its helpers
    are used from another.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Fallback to the import-time engine when no loop is running.
        return engine

    existing = _engines_by_loop.get(loop)
    if existing is None:
        existing = _make_engine()
        _engines_by_loop[loop] = existing
    return existing


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
    )


# Backwards-compatible module-level sessionmaker. Tests patch this directly.
AsyncSessionLocal = _get_session_maker()


def _get_audit_log_table() -> type[SQLModel]:
    from governance_controller.models.audit_log import AuditLog

    return AuditLog


async def dispose_engines(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Dispose per-loop engines and the module-level fallback engine.

    If ``loop`` is provided, only that loop's engine is disposed and evicted
    from the cache. This is the recommended pattern for callers that create a
    fresh event loop, run Controller DB work, and then tear down: dispose the
    engine while still inside the same loop so pool connections close cleanly,
    and remove the entry from ``_engines_by_loop`` immediately instead of waiting
    for GC.

    When called without a loop, all cached engines and the fallback engine are
    disposed.
    """
    if loop is not None:
        eng = _engines_by_loop.pop(loop, None)
        if eng is not None:
            await eng.dispose()
        return

    for eng in list(_engines_by_loop.values()):
        await eng.dispose()
    _engines_by_loop.clear()
    await engine.dispose()


def dispose_engines_sync(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Synchronous wrapper around :func:`dispose_engines`.

    Runs the async disposal inside the provided loop (or the current loop),
    which avoids crossing into a different loop and triggering spurious
    ``attached to a different loop``/``Event loop is closed`` errors.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

    if loop.is_running():
        asyncio.ensure_future(dispose_engines(loop))
    else:
        loop.run_until_complete(dispose_engines(loop))


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
    async with get_engine().begin() as conn:
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

        # Add any missing columns to the execution table (#268).
        execution_columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("execution")
        )
        execution_column_names = {c["name"] for c in execution_columns}
        if "status_error" not in execution_column_names:
            await conn.execute(
                text("ALTER TABLE execution ADD COLUMN status_error VARCHAR")
            )
        cancellation_pending_added = False
        if "cancellation_pending" not in execution_column_names:
            await conn.execute(
                text(
                    "ALTER TABLE execution ADD COLUMN cancellation_pending "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            cancellation_pending_added = True
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_execution_cancellation_pending "
                "ON execution (cancellation_pending, id)"
            )
        )

        if dialect_name == "postgresql" and cancellation_pending_added:
            # Existing pending markers predate the queue flag. Backfill them
            # once at migration time; polling itself never scans audit history.
            await conn.execute(
                text(
                    """
                    UPDATE execution AS e
                    SET macro_agent_run_id = COALESCE(
                            e.macro_agent_run_id,
                            p.macro_agent_run_id
                        ),
                        cancellation_pending = TRUE
                    FROM (
                        SELECT DISTINCT ON (p.execution_id)
                            p.execution_id,
                            NULLIF(p.payload->>'macro_agent_run_id', '')
                                AS macro_agent_run_id
                        FROM auditlog AS p
                        WHERE p.event_type = 'execution_cancel_pending'
                          AND p.execution_id IS NOT NULL
                          AND NULLIF(p.payload->>'macro_agent_run_id', '')
                                IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1
                              FROM auditlog AS c
                              WHERE c.event_type = 'execution_cancel_completed'
                                AND c.task_id = p.task_id
                                AND NULLIF(c.payload->>'macro_agent_run_id', '')
                                    = NULLIF(
                                        p.payload->>'macro_agent_run_id', ''
                                    )
                          )
                        ORDER BY p.execution_id, p.id DESC
                    ) AS p
                    WHERE e.id = p.execution_id
                      AND (
                          e.macro_agent_run_id IS NULL
                          OR e.macro_agent_run_id = p.macro_agent_run_id
                      )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE execution AS e
                    SET cancellation_pending = TRUE
                    FROM (
                        SELECT DISTINCT ON (
                            p.task_id,
                            NULLIF(p.payload->>'macro_agent_run_id', '')
                        )
                            p.task_id,
                            NULLIF(p.payload->>'macro_agent_run_id', '')
                                AS macro_agent_run_id
                        FROM auditlog AS p
                        WHERE p.event_type = 'execution_cancel_pending'
                          AND p.execution_id IS NULL
                          AND NULLIF(p.payload->>'macro_agent_run_id', '')
                                IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1
                              FROM auditlog AS c
                              WHERE c.event_type = 'execution_cancel_completed'
                                AND c.task_id = p.task_id
                                AND NULLIF(c.payload->>'macro_agent_run_id', '')
                                    = NULLIF(
                                        p.payload->>'macro_agent_run_id', ''
                                    )
                          )
                        ORDER BY
                            p.task_id,
                            NULLIF(p.payload->>'macro_agent_run_id', ''),
                            p.id DESC
                    ) AS p
                    WHERE e.task_id = p.task_id
                      AND e.macro_agent_run_id = p.macro_agent_run_id
                    """
                )
            )

            unrecoverable = (
                (await conn.execute(
                    text(
                        """
                        SELECT
                            p.event_id,
                            p.task_id,
                            p.execution_id,
                            'no_matching_execution' AS reason
                        FROM auditlog AS p
                        WHERE p.event_type = 'execution_cancel_pending'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM auditlog AS c
                              WHERE c.event_type = 'execution_cancel_completed'
                                AND c.task_id = p.task_id
                                AND NULLIF(c.payload->>'macro_agent_run_id', '')
                                    = NULLIF(
                                        p.payload->>'macro_agent_run_id', ''
                                    )
                          )
                          AND (
                              NULLIF(
                                  p.payload->>'macro_agent_run_id', ''
                              ) IS NULL
                              OR NOT EXISTS (
                                  SELECT 1
                                  FROM execution AS e
                                  WHERE (
                                      p.execution_id IS NOT NULL
                                      AND e.id = p.execution_id
                                      AND (
                                          e.macro_agent_run_id IS NULL
                                          OR e.macro_agent_run_id = NULLIF(
                                              p.payload->>'macro_agent_run_id',
                                              ''
                                          )
                                      )
                                  )
                                  OR (
                                      p.execution_id IS NULL
                                      AND e.task_id = p.task_id
                                      AND e.macro_agent_run_id = NULLIF(
                                          p.payload->>'macro_agent_run_id', ''
                                      )
                                  )
                              )
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM auditlog AS u
                              WHERE u.event_type =
                                  'execution_cancel_unrecoverable'
                                AND u.payload->>'pending_event_id' = p.event_id
                          )
                        ORDER BY p.id
                        """
                    )
                )).mappings()
                .all()
            )
            if unrecoverable:
                from governance_controller.models.audit_log import (
                    _AUDITLOG_TIP_LOCK_KEY,
                    AuditLog,
                )

                await conn.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _AUDITLOG_TIP_LOCK_KEY},
                )
                previous_hash = (
                    await conn.execute(
                        text(
                            "SELECT row_hash FROM auditlog "
                            "ORDER BY id DESC LIMIT 1"
                        )
                    )
                ).scalar_one_or_none() or ""
                for marker in unrecoverable:
                    pending_event_id = str(marker["event_id"])
                    entry = AuditLog(
                        event_id=str(uuid4()),
                        event_type="execution_cancel_unrecoverable",
                        task_id=str(marker["task_id"]),
                        execution_id=(
                            str(marker["execution_id"])
                            if marker["execution_id"] is not None
                            else None
                        ),
                        actor="system",
                        source="run_migrations",
                        timestamp=datetime.now(UTC),
                        payload={
                            "pending_event_id": pending_event_id,
                            "reason": str(marker["reason"]),
                        },
                        previous_hash=previous_hash,
                    )
                    row_hash = entry.compute_hash()
                    entry.row_hash = row_hash
                    await conn.execute(
                        insert(AuditLog).values(
                            event_id=entry.event_id,
                            event_type=entry.event_type,
                            task_id=entry.task_id,
                            execution_id=entry.execution_id,
                            actor=entry.actor,
                            source=entry.source,
                            timestamp=entry.timestamp,
                            payload=entry.payload,
                            previous_hash=entry.previous_hash,
                            row_hash=row_hash,
                        )
                    )
                    previous_hash = row_hash

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
    async with get_engine().begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await run_migrations()


async def ensure_sqlite_tables() -> None:
    """Create all tables for in-memory SQLite on the async connection."""
    if not settings.database_url.startswith("sqlite"):
        return
    async with get_engine().begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession]:
    if settings.database_url.startswith("sqlite"):
        await ensure_sqlite_tables()
    async with _get_session_maker()() as session:
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
    async with _get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
