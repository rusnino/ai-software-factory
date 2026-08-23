"""Append-only audit log SQLModel entity."""

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import DDL, Column, DateTime, Index, event
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper
from sqlalchemy.sql.expression import ColumnElement
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


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

    _hash_fields: tuple[str, ...] = (
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
        for field in self._hash_fields:
            value = getattr(self, field)
            encoded = self._canonical_value(value)
            digest.update(f"{field}={encoded}\n".encode())
        return digest.hexdigest()


@event.listens_for(AuditLog, "before_insert")
def _audit_log_before_insert(
    mapper: Mapper[Any], connection: Connection, target: AuditLog
) -> None:
    """Hash-chain new audit rows before they are inserted.

    On PostgreSQL the tip lookup uses ``FOR UPDATE`` so concurrent transactions
    serialize on the previous row rather than reading the same tip and forking
    the chain.
    """
    from sqlalchemy import select

    if target.row_hash is None:
        if target.previous_hash == "":
            stmt = (
                select(cast(ColumnElement[str], AuditLog.row_hash))
                .order_by(cast(ColumnElement[int], AuditLog.id).desc())
                .limit(1)
            )
            if connection.dialect.name == "postgresql":
                stmt = stmt.with_for_update()
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
        RAISE EXCEPTION 'AuditLog rows are append-only and cannot be %', TG_OP;
    END;
    $$ LANGUAGE plpgsql
    """
)

_AUDITLOG_POSTGRES_TRIGGER = DDL(  # type: ignore[no-untyped-call]
    """
    DROP TRIGGER IF EXISTS auditlog_block_update_delete ON auditlog;
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
    {_AUDITLOG_POSTGRES_FUNCTION, _AUDITLOG_POSTGRES_TRIGGER}
)

for _stmt in (
    _AUDITLOG_POSTGRES_FUNCTION,
    _AUDITLOG_POSTGRES_TRIGGER,
    _AUDITLOG_SQLITE_DROP_UPDATE,
    _AUDITLOG_SQLITE_DROP_DELETE,
    _AUDITLOG_SQLITE_UPDATE_TRIGGER,
    _AUDITLOG_SQLITE_DELETE_TRIGGER,
):
    event.listen(
        AuditLog.__table__,  # type: ignore[attr-defined]
        "after_create",
        _stmt.execute_if(
            dialect=(
                "postgresql" if _stmt in _POSTGRES_STMTS else "sqlite"
            )
        ),
    )
