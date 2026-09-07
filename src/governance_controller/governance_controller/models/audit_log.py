"""Append-only audit log SQLModel entity."""

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import DDL, Column, DateTime, Index, event, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Mapper
from sqlalchemy.sql.expression import ColumnElement
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


_HASH_FIELDS: tuple[str, ...] = (
    "event_id",
    "event_type",
    "task_id",
    "execution_id",
    "actor",
    "source",
    "timestamp",
    "payload",
    "previous_hash",
)


class AuditLog(SQLModel, table=True):
    """An append-only, hash-chained audit log entry.

    The ``previous_hash`` column links each row to its chronological
    predecessor; ``row_hash`` is a digest over all integrity fields.
    Hash-chain and immutability are enforced via SQLAlchemy events and DB-level
    triggers that also block Core-style bulk UPDATE/DELETE statements.
    """

    __table_args__ = (
        Index("ix_audit_log_event_id", "event_id"),
        Index("ix_audit_log_task_id", "task_id"),
        # Sargable index for stuck-execution pollers that order and filter
        # ``execution_*`` / ``plane_projection_*`` audit markers by ``id``.
        Index("ix_audit_log_event_type_id", "event_type", "id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(index=True)
    event_type: str
    task_id: str = Field(index=True)
    execution_id: str | None = None
    actor: str
    source: str
    timestamp: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON()),
    )
    previous_hash: str = ""
    row_hash: str | None = None

    @staticmethod
    def _canonical_value(value: Any) -> str:
        """Return a deterministic string representation of *value* for hashing.

        SQLite strips timezone info from datetimes when round-tripping, so
        naive datetimes are treated as UTC to keep hashes stable across DB
        dialects.
        """
        if isinstance(value, dict):
            return str(sorted(value.items()))
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC).isoformat()
            return value.isoformat()
        if value is None:
            return ""
        return str(value)

    def compute_hash(self) -> str:
        """Compute an integrity hash over this entry's content."""
        digest = hashlib.sha256()
        for field in _HASH_FIELDS:
            value = getattr(self, field)
            encoded = self._canonical_value(value)
            digest.update(f"{field}={encoded}\n".encode())
        return digest.hexdigest()


# Advisory lock key used to serialize hash-chain tip advancement. The value is
# arbitrary but must be stable; it reserves a single Postgres advisory lock
# namespace for audit-log writes. ponytail: global lock; split by chain only
# when audit throughput makes serialization measurable.
_AUDITLOG_TIP_LOCK_KEY: int = 0xA471_100_0_0001
_SQLITE_AUDIT_TRANSACTION_KEY = "auditlog_sqlite_transaction"


def _acquire_sqlite_audit_transaction(connection: Connection) -> None:
    """Take SQLite's writer lock without committing the caller's transaction.

    A flush may already have written another table before this mapper hook runs;
    in that case SQLite already holds its writer lock and rejects a second
    ``BEGIN IMMEDIATE``. That rejection is safe to ignore, but other SQLite
    errors must still propagate.
    """
    transaction = connection.get_transaction()
    if connection.info.get(_SQLITE_AUDIT_TRANSACTION_KEY) is transaction:
        return

    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError as exc:
        if "cannot start a transaction within a transaction" not in str(exc):
            raise
    connection.info[_SQLITE_AUDIT_TRANSACTION_KEY] = connection.get_transaction()


@event.listens_for(AuditLog, "before_insert")
def _audit_log_before_insert(
    mapper: Mapper[Any], connection: Connection, target: AuditLog
) -> None:
    """Hash-chain new audit rows before they are inserted.

    Concurrent inserters serialize via ``pg_advisory_xact_lock`` on Postgres
    before reading the current chain tip. A bare ``SELECT ... ORDER BY id DESC
    LIMIT 1 FOR UPDATE`` is not sufficient under READ COMMITTED: all blocked
    transactions can re-lock the same stale tip after the blocker commits,
    silently forking the chain (#280).
    """
    from sqlalchemy import select

    if target.row_hash is None:
        if target.previous_hash == "":
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _AUDITLOG_TIP_LOCK_KEY},
                )
            elif connection.dialect.name == "sqlite":
                _acquire_sqlite_audit_transaction(connection)
            stmt = (
                select(cast(ColumnElement[str], AuditLog.row_hash))
                .order_by(cast(ColumnElement[int], AuditLog.id).desc())
                .limit(1)
            )
            result = connection.execute(stmt)
            previous = result.scalar()
            target.previous_hash = previous or ""
        target.row_hash = target.compute_hash()


@event.listens_for(AuditLog, "before_update")
def _audit_log_reject_update(
    mapper: Mapper[Any], connection: Connection, target: AuditLog
) -> None:
    """Raise an exception if anything tries to mutate an audit row."""
    raise RuntimeError("AuditLog rows are append-only and cannot be updated")


@event.listens_for(AuditLog, "before_delete")
def _audit_log_reject_delete(
    mapper: Mapper[Any], connection: Connection, target: AuditLog
) -> None:
    """Raise an exception if anything tries to delete an audit row."""
    raise RuntimeError("AuditLog rows are append-only and cannot be deleted")


# DB-level triggers block Core-style bulk UPDATE/DELETE that bypass the ORM
# mapper events above. The triggers are created automatically with the table
# and are also applied by run_migrations() to existing databases. Each DDL
# object contains a single statement so the async SQLite driver can execute
# it.

# mypy cannot see DDL constructor typing from the SQLAlchemy stubs; these are
# runtime DDL objects only.
_AUDITLOG_POSTGRES_FUNCTION = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE OR REPLACE FUNCTION auditlog_block_update_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'AuditLog rows are append-only and cannot be %%', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """
)

_AUDITLOG_POSTGRES_DROP_TRIGGER = DDL(  # type: ignore[no-untyped-call]
    "DROP TRIGGER IF EXISTS auditlog_block_update_delete ON auditlog"
)
_AUDITLOG_POSTGRES_CREATE_TRIGGER = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER auditlog_block_update_delete
        BEFORE UPDATE OR DELETE ON auditlog
        FOR EACH ROW EXECUTE FUNCTION auditlog_block_update_delete()
    """
)

_AUDITLOG_SQLITE_DROP_UPDATE = DDL(  # type: ignore[no-untyped-call]
    "DROP TRIGGER IF EXISTS auditlog_block_update"
)
_AUDITLOG_SQLITE_DROP_DELETE = DDL(  # type: ignore[no-untyped-call]
    "DROP TRIGGER IF EXISTS auditlog_block_delete"
)
_AUDITLOG_SQLITE_UPDATE_TRIGGER = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER auditlog_block_update
        BEFORE UPDATE ON auditlog
    BEGIN
        SELECT RAISE(ABORT, 'AuditLog rows are append-only and cannot be updated');
    END
    """
)
_AUDITLOG_SQLITE_DELETE_TRIGGER = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER auditlog_block_delete
        BEFORE DELETE ON auditlog
    BEGIN
        SELECT RAISE(ABORT, 'AuditLog rows are append-only and cannot be deleted');
    END
    """
)

_POSTGRES_STMTS: frozenset[DDL] = frozenset(
    {
        _AUDITLOG_POSTGRES_FUNCTION,
        _AUDITLOG_POSTGRES_DROP_TRIGGER,
        _AUDITLOG_POSTGRES_CREATE_TRIGGER,
    }
)

for _stmt in (
    _AUDITLOG_POSTGRES_FUNCTION,
    _AUDITLOG_POSTGRES_DROP_TRIGGER,
    _AUDITLOG_POSTGRES_CREATE_TRIGGER,
    _AUDITLOG_SQLITE_DROP_UPDATE,
    _AUDITLOG_SQLITE_DROP_DELETE,
    _AUDITLOG_SQLITE_UPDATE_TRIGGER,
    _AUDITLOG_SQLITE_DELETE_TRIGGER,
):
    event.listen(
        AuditLog.__table__,  # type: ignore[attr-defined]
        "after_create",
        _stmt.execute_if(
            dialect=("postgresql" if _stmt in _POSTGRES_STMTS else "sqlite")
        ),
    )
