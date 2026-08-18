# REVIEW-006: Verification of REVIEW-003/004/005 gap re-fixes

Date: 2026-08-18
Scope: the five gaps still open or reopened after `REVIEW-005` (`GAP-023`, `GAP-024`, `GAP-031`, `GAP-032`,
`GAP-043`). Method: same as prior rounds, with extra weight on `GAP-023` given it had already failed two
prior fix attempts (`REVIEW-004`, `REVIEW-005`) — verified via 100+ independent-session concurrency trials
against the real service code, not just reading the diff or trusting the shipped test.

## Verdict summary

**This mini-review closed successfully against the gate rule**: every `CRITICAL`/`HIGH` gap that was open
going into this round is now genuinely fixed. Two `MEDIUM` gaps have real residual issues and are
downgraded to `IN_PROGRESS` rather than left falsely `CLOSED`.

| Gap | Verdict | New status |
|---|---|---|
| GAP-023 | **FULLY FIXED (third attempt)** — atomic `UPDATE task SET state=..., version=version+1 WHERE id=? AND version=? AND state=?`, checked via `rowcount` before any state mutation or `MacroAgentExecutor.start()` call, same transaction as the rest of the request. Independently reproduced at 2-way and 5-way concurrency, 100+ trials with fully separate sessions/engines (no shared identity map, the exact thing that broke attempt 2), always exactly one winner, never a double-trigger | CLOSED (unchanged, now genuinely earned) |
| GAP-024 | **FULLY FIXED (second attempt)** — `PolicyEngine` now validates `TaskContract.verification["commands"]` with the same denylist logic as `CompletionContract` commands; live-verified with and without a `completion_contract` present; confirmed no third command-execution surface exists anywhere in the codebase | CLOSED (unchanged) |
| GAP-031 | **PARTIALLY FIXED** — happy path (`controller_execution_id` propagation) genuinely works. Failure path's claimed behavior is false: an uncaught `RuntimeError` from `executor.start()` reaches `get_db()`'s rollback and erases the entire transaction — Execution row, FAILED marking, Task state, and audit trail all vanish, as if the approval never happened | → reopened IN_PROGRESS |
| GAP-032 | **FULLY FIXED** — `ExecutionConfig.role` + `PolicyEngine` harness-registry lookup against `allowed_roles`, live-verified against the exact SPEC-06 §6.2 claude-code/worker (rejected) vs claude-code/planner (allowed) scenario; no default-value regression in existing fixtures | CLOSED (unchanged) |
| GAP-043 | **PARTIALLY FIXED** — the `except BaseException` change is real and harmless, but mutation testing proved none of the new tests (including the one built for this exact purpose) actually discriminate it from the old `except Exception:` | → reopened IN_PROGRESS |

Final sanity check independently confirmed: `156 passed`, `ruff` clean, `mypy --strict` clean (0 errors).

## The one important new finding: GAP-031's failure path contradicts GAP-022/043's rollback semantics

This is a genuine interaction bug between two otherwise-correct fixes, not a flaw in either one alone.
GAP-022 correctly made `get_db()` roll back the whole transaction on any exception, so a failed request
never leaves partial state. GAP-031 correctly creates the `Execution` row and marks it `FAILED` *in Python*
before re-raising. But `_trigger_execution`'s `RuntimeError` is never caught by `api/approvals.py` (which
only handles `ValueError`), so it propagates to `get_db()`, which — correctly, per its own contract — rolls
back everything, including the `FAILED` marking that was supposed to be the durable record of the incident.
Net effect: if `MacroAgentExecutor.start()` fails, the database ends up exactly as if the approval had
never been submitted — no Execution row, no audit trail, Task back at its pre-approval state. A human
retrying the same approval would see no evidence anything had gone wrong the first time. This doesn't
create a governance-bypass risk (nothing gets wrongly approved), but it is a real audit-completeness gap
against SPEC-03 §3.8 ("every governance event stored"), and the regression test that was supposed to catch
this passes only because it bypasses `get_db()` entirely (calls `ApprovalService.approve()` directly against
a fixture session that's never rolled back).

The fix, when someone picks this up: either catch `RuntimeError` in `api/approvals.py` and return a
structured error (5xx with detail, not raw), and/or have `_trigger_execution` commit the `FAILED` marking
as its own short sub-transaction before propagating the error, rather than letting the outer rollback erase
it.

## Recommendation

No `CRITICAL`/`HIGH` gaps remain open. Given the mini-review's success, a full fresh codebase review
follows this one (`REVIEW-007`), focused on whether the substantial changes this round (atomic
compare-and-swap concurrency control, `BaseException` handling, role enforcement, verification-command
validation) interact correctly with each other and with code not recently touched.

## Verification

`cd src/governance_controller && uv run pytest -q` → 156 passed. `uv run ruff check .` → clean.
`uv run mypy governance_controller` → 0 errors. GAP-023's reproduction used 100+ trials with genuinely
independent SQLAlchemy engines/sessions against a shared file-backed SQLite DB, not the test suite's
fixtures.
