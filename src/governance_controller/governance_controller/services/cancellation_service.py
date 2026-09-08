"""Durable single-flight claims for external execution cancellation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, exists, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.config import settings
from governance_controller.constants import TaskState
from governance_controller.models.execution import Execution
from governance_controller.models.task import Task

# The default macro-agent HTTP timeout is 30 seconds. Ten times that timeout,
# with a five-minute floor, leaves room for p99 cancellation latency and
# scheduler delay without allowing an in-flight claimant to be reaped early.
_CANCELLATION_CLAIM_LEASE_SECONDS = max(
    300.0,
    10 * settings.macro_agent_timeout_seconds,
)
CANCELLATION_CLAIM_LEASE = timedelta(
    seconds=_CANCELLATION_CLAIM_LEASE_SECONDS,
)


@dataclass(frozen=True, slots=True)
class CancellationClaim:
    """A database-owned lease for one external cancellation."""

    execution_id: str
    macro_agent_run_id: str
    token: str
    task_owns_run: bool


async def claim_cancellation(
    db: AsyncSession,
    execution_id: str,
    macro_agent_run_id: str,
    *,
    task_id: str | None = None,
    execution_already_locked: bool = False,
    now: datetime | None = None,
) -> CancellationClaim | None:
    """Atomically claim a pending cancellation or return ``None``.

    The task-pointer decision is part of the same guarded UPDATE as the lease
    acquisition. A successful claim is committed before the caller performs
    external I/O, so SQLite never holds its writer lock over the HTTP request.
    """
    claimed_at = now or datetime.now(UTC)
    lease_cutoff = claimed_at - CANCELLATION_CLAIM_LEASE
    token = str(uuid4())
    execution_columns = Execution.__table__.c  # type: ignore[attr-defined]
    task_columns = Task.__table__.c  # type: ignore[attr-defined]
    task_owns_run = exists(
        select(task_columns.id).where(
            task_columns.id == execution_columns.task_id,
            task_columns.state == TaskState.RUNNING.value,
            task_columns.latest_macro_agent_run_id == macro_agent_run_id,
        )
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        locked_task_id = task_id
        if not execution_already_locked:
            locked_execution = (
                await db.execute(
                    select(Execution)
                    .where(execution_columns.id == execution_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if (
                locked_execution is None
                or locked_execution.macro_agent_run_id != macro_agent_run_id
            ):
                await db.rollback()
                return None
            locked_task_id = locked_execution.task_id
            if task_id is not None and locked_task_id != task_id:
                await db.rollback()
                return None
        if locked_task_id is None:
            await db.rollback()
            return None
        await db.execute(
            select(Task)
            .where(task_columns.id == locked_task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    statement = (
        update(Execution)
        .where(
            execution_columns.id == execution_id,
            execution_columns.macro_agent_run_id == macro_agent_run_id,
            execution_columns.cancellation_pending.is_(True),
            or_(
                execution_columns.cancellation_claim_token.is_(None),
                execution_columns.cancellation_claimed_at.is_(None),
                execution_columns.cancellation_claimed_at < lease_cutoff,
            ),
        )
        .values(
            cancellation_claim_token=token,
            cancellation_claimed_at=claimed_at,
        )
        .returning(
            case(
                (task_owns_run, literal(True)),
                else_=literal(False),
            ).label("task_owns_run")
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(statement)
    row = result.one_or_none()
    if row is None:
        await db.rollback()
        return None

    await db.commit()
    return CancellationClaim(
        execution_id=execution_id,
        macro_agent_run_id=macro_agent_run_id,
        token=token,
        task_owns_run=bool(row._mapping["task_owns_run"]),
    )


async def release_cancellation_claim(
    db: AsyncSession,
    claim: CancellationClaim,
    *,
    completed: bool,
) -> bool:
    """Release a claim if this worker still owns it.

    The caller records the corresponding audit outcome and commits this same
    transaction. Failed cancellation keeps the durable queue flag set so a
    later worker can retry after the lease is released.
    """
    execution_columns = Execution.__table__.c  # type: ignore[attr-defined]
    result = await db.execute(
        update(Execution)
        .where(
            execution_columns.id == claim.execution_id,
            execution_columns.macro_agent_run_id == claim.macro_agent_run_id,
            execution_columns.cancellation_pending.is_(True),
            execution_columns.cancellation_claim_token == claim.token,
        )
        .values(
            cancellation_pending=completed is False,
            cancellation_claim_token=None,
            cancellation_claimed_at=None,
        )
        .execution_options(synchronize_session="fetch")
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
