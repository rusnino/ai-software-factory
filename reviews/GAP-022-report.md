# GAP-022 Fix Report

## Problem

`session.commit()` was never called anywhere in the codebase. Every write path
(Task, Approval, AuditLog, Execution) depended on the per-request `get_db()`
FastAPI dependency, which only yielded an `AsyncSession` and left it open.
When running against real PostgreSQL, the session closed at request end without
committing, so all writes were discarded. Existing tests passed only because the
test fixture started a single `session.begin()` transaction and shared it across
all requests; `flush()` inside that transaction made reads see uncommitted data.

## Root Cause

`governance_controller/db.py::get_db()` did not commit on success or rollback on
exception. FastAPI dependencies using `yield` are cleaned up after the route
returns, so this generator was the natural place to ensure durability.

## Fix

Updated `get_db()` to:

- `yield session`
- `await session.commit()` if the request handler returns successfully
- `await session.rollback()` if the handler raises, then re-raise the exception

This makes every route that depends on `get_db()` durable without requiring
explicit commits scattered through services.

No changes were required in `tests/conftest.py`. The existing fixtures manage a
single shared transaction and continue to work because the in-test session is
yielded directly by an override of `get_db()`; the new `commit()` path in the
real `get_db()` is not exercised by those overrides. This is acceptable for unit
and API tests, but a follow-up integration test against PostgreSQL should
confirm real durability.

## Verification

```bash
cd src/governance_controller
uv run pytest tests/ -q
# 134 passed in 1.71s

uv run ruff check governance_controller tests
# All checks passed!
```

## Commit

- `fix(GAP-022): commit per-request DB session so writes persist`

## Concerns

- The health endpoint `health_check()` catches all exceptions to return a 503
  degraded response. With the new `get_db()` behavior, an exception inside the
  route will cause `get_db()` to rollback and re-raise; the route itself swallows
  it and returns the JSON response, so the final `session.commit()` is still
  called. Because the health endpoint only reads, this never produces data writes
  but does initiate an unnecessary empty commit. This is harmless but could be
  cleaned up in a future refactor by not using `get_db()` for a pure read-only
  ping or by using `BEGIN READ ONLY` semantics.
- No PostgreSQL-backed integration test was added in this change; the fix was
  verified at the unit/API level only. A later test running against a real
  database should confirm that a task created via the API survives a new
  session.
