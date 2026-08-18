# GAP-023 Report

## Status

CLOSED by `b411b58` (ledger update `d35eba6`).

## Problem

`POST /approvals` did not lock the `Task` row or versioning before mutating state.
Two concurrent approvals for the same task with distinct idempotency keys could:

1. Pass policy/permission checks in parallel.
2. Both invoke `StateMachine.transition()`.
3. Both call `MacroAgentExecutor.start()`, double-triggering a real macro-agent run.

## Root Cause

- `TaskService.get_by_id()` used a plain `SELECT`.
- `ApprovalService.approve()` compared state against an in-memory `Task` object with
  no DB-level coordination.
- There was no version/optimistic-concurrency guard.

## Changes

### Model

- `governance_controller/models/task.py`
  - Added `version: int = Field(default=0)` column.

### Service

- `governance_controller/services/task_service.py`
  - Added `get_by_id_for_update()` using `with_for_update()` for callers that need a
    DB-level row lock.

- `governance_controller/services/approval_service.py`
  - After idempotency check, re-reads the task with `SELECT ... FOR UPDATE`.
  - Verifies the locked row's `version` equals the in-memory task's `version`.
  - On mismatch, logs an audit event and raises `ValueError`.
  - On success, increments `task.version` so concurrent callers see a stale version.

### API

- `governance_controller/api/approvals.py`
  - Reads the task via `get_by_id_for_update()` before invoking
    `ApprovalService.approve()`.

### Tests

- Updated unit tests in `test_approval_service.py`, `test_execution_trigger.py`, and
  `test_audit_log.py` to persist tasks to the DB before approving (required because
  `approve()` now re-fetches by primary key).
- Added `tests/test_approval_concurrency.py`:
  - Fires two concurrent `EXECUTION` approvals with different idempotency keys.
  - Asserts only one succeeds, only one `MacroAgentExecutor.start()` call occurs,
    only one `Execution` and one `Approval` row are created, and the task ends in
    `RUNNING`.

## Verification

```bash
cd src/governance_controller
uv run pytest tests/ -q
# 135 passed

uv run ruff check governance_controller tests
# All checks passed
```

## Concerns

- `with_for_update()` is honored by PostgreSQL but ignored by SQLite/aiosqlite.
  The regression test currently runs on SQLite using SQLAlchemy's async session and
  nested transactions; the version check provides the cross-engine guard. The lock
  provides the additional serialization when running on the intended PostgreSQL
  backend.
- GAP-022 (missing `session.commit()`) remains OPEN. Row locking plus version
  checking reduce the double-trigger window, but durable writes still depend on a
  commit path being added separately.
