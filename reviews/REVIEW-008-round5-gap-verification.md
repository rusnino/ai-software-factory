# REVIEW-008: Verification of REVIEW-006/007 gap fixes

Date: 2026-08-18
Scope: all 10 gaps closed after REVIEW-007 (`GAP-044` through `GAP-053`). Method: same as prior rounds —
independently re-read diffs and current file state, ran tests live, and for the two most consequential
claims (`GAP-044`, `GAP-046`/`GAP-047`) reproduced the exact scenarios against real session/DB semantics
rather than trusting the checked-in test suite.

## Verdict summary

| Gap | Verdict | New status |
|---|---|---|
| GAP-044 | **NOT ACTUALLY FIXED** — the commit-guard condition is always false on the real request path; audit rows for rejected approvals are still erased. Live-reproduced for all 4 original scenarios | → reopened OPEN |
| GAP-045 | **FULLY FIXED** — verification commands now always execute via real subprocess, live-verified with failing and passing commands | CLOSED (unchanged) |
| GAP-046 | **PARTIALLY FIXED** — the CAS mechanism itself is correct at `_trigger_execution`/`verify_and_advance`, but `EventBridge.handle()` never raises on CAS failure (advisory-only), and a new bug was found in `atomic_transition()` itself (corrupts the in-memory object on failure) | → reopened OPEN |
| GAP-047 | **PARTIALLY FIXED** — the route exists and is wired correctly, but has no auth, no schema, zero test coverage, and sits on top of GAP-046's unresolved half — the review's warning about wiring EventBridge live was not fully addressed, just changed in character | → reopened OPEN |
| GAP-048 | FULLY FIXED — confirmed via `docker compose config` and live `Settings` resolution | CLOSED (unchanged) |
| GAP-049 | FULLY FIXED — default-deny for unregistered harnesses, live-verified | CLOSED (unchanged) |
| GAP-050 | FULLY FIXED — stale references corrected, cross-checked against the actual ledger | CLOSED (unchanged) |
| GAP-051 | FULLY FIXED — all four fields now flow into the outbound macro-agent payload | CLOSED (unchanged) |
| GAP-052 | FULLY FIXED — real `server_default="0"` added | CLOSED (unchanged) |
| GAP-053 | FULLY FIXED — doc corrected, classes disambiguated, no stale references | CLOSED (unchanged) |

`166 passed`, `ruff` clean, `mypy --strict` clean — all independently re-confirmed exact.

## The two substantial findings

### GAP-044 — the fix's own guard condition can never be true in production

`_log_rejection_and_raise()` only commits the audit row `if self.db.get_transaction() is None`. But
`api/approvals.py` calls `task_service.get_by_id_for_update()` — a `SELECT ... FOR UPDATE` — *before*
`ApprovalService.approve()` is ever invoked, and that `SELECT` autobegins a SQLAlchemy transaction on the
session. By the time any rejection branch inside `approve()` runs, a transaction is always already open, so
the guard is always false and the commit branch is unreachable dead code on the real request path. Live
reproduction (driving the real, unmodified `get_db()` exactly as FastAPI does) confirmed the audit row is
still erased for self-approval, permission-denied, policy-violation, and concurrent-modification rejections
— the original bug, unchanged. The new tests pass only because they either operate inside their own
explicit `session.begin()` (never taking the "commit" branch either, but reading the row before their own
teardown rollback) or because one test manually calls `session_a.commit()` itself — a step production code
never performs.

### GAP-046/047 — the database is safe; the HTTP/event layer around it is not

Across every concurrency reproduction run (multiple, including the full two-`get_db()`-session production
lifecycle), the database itself never ended up double-applied or overwritten — GAP-023's and this fix's core
compare-and-swap mechanism holds. But `EventBridge.handle()`'s use of it doesn't raise on failure; it just
notes the conflict in an audit payload field and returns normally, so `POST /events` responds `204` to a
request whose transition was actually rejected. Combined with `_record_processed_event()` running
unconditionally, the losing event is marked "processed" and can never be retried — a silent, permanent,
misleadingly-successful event loss. Separately, `atomic_transition()` has its own bug: SQLAlchemy's
`synchronize_session="evaluate"` still applies the `UPDATE`'s `SET` values to the in-memory object even when
`rowcount` is 0, so a failed call leaves the caller's `task` object showing a state that was never actually
persisted. This didn't cause a spurious write in any reproduction (the object is marked "clean," not dirty),
but it's a real trap for any future code that trusts the object after a failed `atomic_transition()` call
without checking the boolean return.

## Recommendation

3 of the 5 previously-reopened `HIGH` gaps are genuinely closed (`GAP-045`, plus the unrelated `GAP-048`).
`GAP-044`, `GAP-046`, and `GAP-047` remain open with concrete, reproduced defects and clear fix shapes:
`GAP-044` needs the commit to happen unconditionally (or the guard logic replaced with something that
actually distinguishes "will this rollback erase what I just wrote" — autobegin makes that harder than it
looks); `GAP-046`/`GAP-047` need `EventBridge.handle()` to actually raise on CAS failure (mapped to a
retriable HTTP status, not `204`), the idempotency record to only be written on successful transitions, and
`atomic_transition()` to either refresh/expire the object on failure or document that its return value, not
the object, is the only trustworthy signal.

## Verification

`cd src/governance_controller && uv run pytest -q` → 166 passed (unchanged; no application code modified by
this review). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors. GAP-044's and
GAP-046/047's reproductions drove real, unmodified session/generator objects exactly as FastAPI does, not
the test suite's fixtures — the same technique that has caught every genuinely-unfixed claim across this
review series so far.
