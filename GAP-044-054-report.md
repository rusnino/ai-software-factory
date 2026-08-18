# GAP-044 / GAP-054 Fix Report

## Status

CLOSED in commit `3e93928`.

## Root cause

`_trigger_execution` in `services/approval_service.py` raised `ValueError` / `RuntimeError` without first committing the work it had already done inside the same request:

- The legitimate `PLAN_APPROVED -> EXEC_APPROVED` transition and `Approval` row written by `approve()`.
- The `EXEC_APPROVED -> READY` transition and the `Execution` row created in `_trigger_execution`.
- The audit log entries for those transitions.
- On executor crash, the `READY -> FAILED` transition and the `execution_start_failed` audit entry.

Because production code uses `get_db()` without `begin()`, an unhandled exception causes `get_db()` to roll back the entire transaction, erasing all of the above.

GAP-054 was the missing audit entry at the `RUNNING`-CAS failure site (lines 397-400), which logged nothing at all.

## Changes

### `src/governance_controller/governance_controller/services/approval_service.py`

For every exception path in `_trigger_execution`, added `await self.db.commit()` after logging and before raising:

1. **READY-CAS failure** — logs `concurrent_modification`, commits, raises `ValueError`.
2. **Executor crash (`except Exception`)** — logs `execution_start_failed` on successful FAILED-CAS, or `concurrent_modification` if FAILED-CAS also lost, commits, raises `RuntimeError`.
3. **RUNNING-CAS failure** — added the missing `concurrent_modification` audit log (GAP-054), commits, raises `ValueError`.

### `src/governance_controller/tests/test_approval_concurrency.py`

Added two regression tests that drive the real `get_db()` generator and query state from a fresh session:

- `test_executor_failure_path_survives_get_db_rollback` — fake executor raises during `POST /approvals`-equivalent EXECUTION approval; asserts durable `FAILED` task state, `Execution` row, and audit entries.
- `test_ready_cas_loss_survives_get_db_rollback` — a stale reader loses the READY CAS against a winning session; asserts the loser's audit entry persists and the database reflects the winner's `RUNNING` state.

## Verification

- `uv run pytest tests/ -q` → 173 passed.
- `uv run ruff check governance_controller tests` → clean.
- `uv run mypy --strict governance_controller` → clean.

## Commit

```text
3e93928 fix(GAP-044/054): commit execution failure/concurrent-modification state before raising in _trigger_execution
```

## Concerns

- The service-level `await self.db.commit()` is safe because production `get_db()` does not use `begin()`. Test fixtures already avoid `begin()` for this reason. Any future caller that wraps the service in `async with session.begin()` will need to avoid explicit commits, or the same pattern used in `_log_rejection_and_raise` will break; current callers all use the `get_db()` generator.
- `api/approvals.py` still only catches `ValueError` from `approval_service.approve()` and maps it to HTTP 409/422/403. The executor-crash `RuntimeError` therefore propagates as an unhandled 500. This matches the existing behavior and is outside the scope of GAP-044/GAP-054, but should be addressed when the API's error-mapping contract is next reviewed.
