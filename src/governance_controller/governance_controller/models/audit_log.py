"""Append-only audit log SQLModel entity."""

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Column, DateTime, Index, event
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
    Hash-chain and immutability are enforced via SQLAlchemy events as a
    portable alternative to DB-specific triggers.
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
    """Hash-chain new audit rows before they are inserted."""
    from sqlalchemy import select

    if target.row_hash is None:
        if target.previous_hash == "":
            result = connection.execute(
                select(cast(ColumnElement[str], AuditLog.row_hash))
                .order_by(cast(ColumnElement[int], AuditLog.id).desc())
                .limit(1)
            )
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
