# REVIEW-005: Verification of REVIEW-003/004 gap re-fixes

Date: 2026-08-18
Scope: every commit claiming to close a gap from `REVIEW-003`/`REVIEW-004` (GAP-020..GAP-042).
Method: same as prior rounds — independently re-read diffs and current file state, ran tests live, and for
the two highest-stakes claims (GAP-022, GAP-023) wrote standalone reproduction scripts against the real
`db.py`/session machinery rather than trusting the checked-in test suite, since the test fixture has
repeatedly been shown (this round included) to mask exactly this class of bug.

## Verdict summary

**This mini-review did not close successfully.** Per the task's own instruction, a full fresh sweep is
therefore deferred to a later round — see Recommendation below.

| Gap | Verdict | New status |
|---|---|---|
| GAP-020 | FULLY FIXED (minor: no false-positive regression test) | CLOSED (unchanged) |
| GAP-021 | PARTIALLY FIXED — failing-path integration test added; passing-path (`HUMAN_REVIEW`) still not tested through the real `EventBridge` path | → IN_PROGRESS |
| GAP-022 | **FULLY FIXED as a mechanism** — proven by direct reproduction against real session/connection semantics (commit-on-success, rollback-on-exception both confirmed). Left a real, disclosed test-coverage gap → logged separately as `GAP-043` | CLOSED (unchanged); new `GAP-043` opened |
| GAP-023 | **NOT ACTUALLY FIXED** — the "optimistic version guard" is dead code (SQLAlchemy identity-map staleness); the row lock is a no-op on SQLite; the new test passes by lexical coincidence, not because the guard fired | → reopened OPEN |
| GAP-024 | **PARTIALLY FIXED** — `completion_contract.command` genuinely validated now; `TaskContract.verification.commands` is a live, untested, complete bypass of the same check | → reopened OPEN |
| GAP-025 | FULLY FIXED (class-level; still no live route, matching pre-existing dead-code scope, not a regression) | CLOSED (unchanged) |
| GAP-026 | FULLY FIXED — durable DB-backed dedup, bug-locking test rewritten to assert the fix | CLOSED (unchanged) |
| GAP-028 | FULLY FIXED — `mypy --strict` clean, 0 errors, confirmed by real run | CLOSED (unchanged) |
| GAP-029 | FULLY FIXED (via removal — `GitCascadeService` deleted outright, not left orphaned) | CLOSED (unchanged) |
| GAP-030 | FULLY FIXED (moot — removed along with GAP-029) | CLOSED (unchanged) |
| GAP-031 | **PARTIALLY FIXED** — metadata block shape added, but `controller_execution_id` hardcoded `None`; the core traceability defect (execution ID created after the outbound call) persists, and the new test locks the null value in as "expected" | → reopened OPEN |
| GAP-032 | **PARTIALLY FIXED** — `allowed_roles` values corrected to match SPEC-06 §6.2; the field remains unread/unenforced anywhere, the original complaint's substance | → reopened OPEN |
| GAP-033 | FULLY FIXED — real 404 test added | CLOSED (unchanged) |
| GAP-034 | FULLY FIXED — real non-root user, `chown`'d `/app`, `USER` directive before `CMD` | CLOSED (unchanged) |
| GAP-038 | FULLY FIXED — transition actually removed from the table, not just guarded | CLOSED (unchanged) |
| GAP-040 | FULLY FIXED — explicit, `Settings`-sourced, configurable timeout | CLOSED (unchanged) |

`docs/NEXT_STEPS.md`'s "142 passed, ruff clean, mypy --strict clean" claim was independently re-run and
confirmed exact on all three counts.

## The two most important findings

### GAP-023 (HIGH, reopened) — the concurrency fix doesn't work, for a subtle and instructive reason

`ApprovalService.approve()` re-fetches the `Task` under `.with_for_update()` on the *same session* that
already holds the original object. Because `AsyncSessionLocal` is configured with `expire_on_commit=False`
and the re-fetch doesn't pass `populate_existing=True` (or call `session.refresh()`), SQLAlchemy's identity
map returns the *identical Python object* already in memory — not a value re-read from the database. So
`locked_task.version != task.version` compares an object against itself and can never be `True`. Proven by
direct reproduction: a session holding a stale `Task` (version 0), after a second session commits a real
concurrent approval (version 1), still sees version 0 on re-fetch-under-lock. Separately, `.with_for_update()`
compiles to a plain `SELECT` with no `FOR UPDATE` clause at all on SQLite (confirmed by inspecting the
compiled statement against both dialects) — the row lock does nothing in the test environment, which is
SQLite by default. The new `tests/test_approval_concurrency.py` passes, but only because both "concurrent"
coroutines share one `AsyncSession`, and the second one crashes into SQLAlchemy's own
"concurrent operations are not permitted" session-reuse guard — an assertion checking for the substring
`"concurrent"` in the error message passes by coincidence, not because the intended mechanism engaged.

### GAP-024 (HIGH, reopened) — fixed one field, missed its twin

The fix for `completion_contract.command` validation is real and well-tested — verified by executing a lied
`destructive_shell=False` + `rm -rf ~` contract through the actual `PolicyEngine.evaluate()` and confirming
rejection. But `TaskContract.verification["commands"]` — literally the field named in the original finding
as "also accepted by the schema but read nowhere" — was wired into `VerificationService` *without* adding
the equivalent `PolicyEngine` check. Proven live: a contract with a benign `completion_contract` but a
malicious command placed in `verification.commands` sails through policy evaluation with zero violations,
then executes via raw shell subprocess. Zero test coverage exists for this field on either the policy or
verification side.

## Recommendation

Because `GAP-023` (HIGH) is not fixed at all and `GAP-024` (HIGH) has a live, complete bypass, this
mini-review has **not** closed successfully. Per instruction, a fourth full-codebase sweep is deferred until
these (and the reopened `GAP-031`/`GAP-032`/`GAP-021`/`GAP-043`) are addressed — re-running a full review now
would likely just re-surface the same root causes plus whatever's already known, without adding information.
`docs/NEXT_STEPS.md`'s "Phase 1 is complete" claim is corrected again as part of this review.

## Verification

`cd src/governance_controller && uv run pytest -q` → 142 passed (unchanged by this review — no application
code was modified). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors. The
GAP-022/GAP-023 reproductions were run as standalone scripts against real session/connection objects, not
against the test suite's fixtures, which is precisely how the underlying bugs stayed hidden this long.
