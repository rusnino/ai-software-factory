# REVIEW-010: Verification of REVIEW-009 gap re-fixes

Date: 2026-08-18
Scope: the two gaps still open after REVIEW-009 (`GAP-044`, `GAP-054`) — the fourth attempt at this specific
class of bug (session lifecycle vs. exception paths in `_trigger_execution`). Method: same as prior rounds,
maximum skepticism given the track record — the fix was assumed broken until personally reproduced against
the real, unmodified `get_db()` generator for all four failure branches, not the checked-in test suite.

## Verdict

**GAP-044 and GAP-054 are genuinely fixed this time.** Commit `3e93928` adds a plain `await self.db.commit()`
immediately before each of `_trigger_execution`'s three `raise` statements (READY-CAS loss,
executor-crash/FAILED-CAS, RUNNING-CAS loss). Independently reproduced all four failure scenarios against
the real `get_db()` generator (driven exactly as FastAPI does, starting from a real
`get_by_id_for_update()` call, re-queried afterward from a fresh session):

- **Executor-crash**: Task ends `FAILED`, `Execution` row `state=FAILED`/`ended_at` set, `execution_start_failed`
  audit row present — all survive.
- **READY-CAS loss** (genuine two-session race): the winner's write survives; the loser's `concurrent_modification`
  audit row also survives.
- **FAILED-CAS loss** and **RUNNING-CAS loss** (forced deterministically — a real third concurrent SQLite
  connection deadlocks against the row lock rather than racing cleanly, itself informative, see below):
  each branch's `concurrent_modification` audit row survives, and critically, the *already-legitimate*
  `Execution` row created earlier in the same request survives alongside it, correctly un-touched.

No double-commit or altered happy-path behavior — the new commits sit only in failure branches, confirmed by
reading the code and by `tests/test_phase1_smoke.py` still passing unchanged.

## The real problem this round: the tests protecting this fix don't work

Both new tests (`test_executor_failure_path_survives_get_db_rollback`,
`test_ready_cas_loss_survives_get_db_rollback`) place `pytest.raises(...)` *inside* the
`async with asynccontextmanager(get_db)() as db:` block. This means the exception is caught by `pytest.raises`
before it ever reaches `get_db()`'s own exception handling — the generator exits its `try` block normally and
takes the **commit** branch, never the `rollback` branch the tests are named after. This was proven, not just
read: reverting any one of the three `3e93928` commit-before-raise fixes individually, one at a time, leaves
the **entire 173-test suite green**, including both tests whose names explicitly claim to catch exactly that
regression. `test_executor_failure_advances_to_failed_with_version_3` independently masks the same thing via
its own leftover manual `db.commit()` (flagged under GAP-055, never removed). And no test exists at all —
correct or not — for the FAILED-CAS-loss or RUNNING-CAS-loss branches specifically; the one test that claims
to cover READY-CAS-loss actually exercises the *outer* `approve()` CAS per its own code comment, not
`_trigger_execution`'s internal one.

Net effect: the code is correct today, verified by direct reproduction — but nothing in CI would notice if a
fifth attempt silently reverted any of these three lines. Logged as `GAP-056` (MEDIUM — a test-coverage gap,
not a live defect, since the underlying code is currently correct).

## A structural observation worth carrying forward

Forcing scenarios (c)/(d) via a genuinely concurrent second database connection deadlocked on SQLite
("database is locked") rather than racing cleanly, because `get_by_id_for_update()` holds a row lock for the
whole request. On Postgres, a competing writer would likewise simply block behind that lock rather than race
it — meaning the FAILED-CAS-loss and RUNNING-CAS-loss branches may be largely defensive/unreachable in
practice on the real target database, not truly racy paths. Worth confirming explicitly before investing
further test effort in them; not filed as its own gap since it's a question, not a proven defect.

## Milestone

With `GAP-044` and `GAP-054` closed, **no `CRITICAL` or `HIGH` gap remains open in `reviews/GAPS.md`** for
the first time across this ten-review series. `GAP-056` (new, MEDIUM) does not block the gate rule.

## Verification

`cd src/governance_controller && uv run pytest -q` → 173 passed. `uv run ruff check .` → clean.
`uv run mypy governance_controller` → 0 errors. All four failure-branch reproductions and the
regression-masking proof (reverting each fix individually and re-running the relevant tests) were run
directly against the real code, not the checked-in fixtures.
