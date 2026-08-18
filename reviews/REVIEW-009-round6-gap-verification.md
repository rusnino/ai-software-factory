# REVIEW-009: Verification of REVIEW-008 gap re-fixes

Date: 2026-08-18
Scope: the 5 gaps reopened or downgraded by REVIEW-008 (`GAP-044`, `GAP-046`, `GAP-047`, `GAP-054`, `GAP-055`).
Method: same as prior rounds — independently re-read diffs and current file state, and for the two most
consequential claims (`GAP-044`'s third attempt, `GAP-046`/`GAP-047`'s second attempt) reproduced the exact
scenarios against real session/DB semantics, not the checked-in test suite.

## Verdict summary

| Gap | Verdict | New status |
|---|---|---|
| GAP-044 | **PARTIALLY FIXED (third attempt).** The four originally-cited `approve()` rejection paths are now genuinely durable — live-reproduced surviving a real `get_db()` rollback for all 4. `_trigger_execution`'s rejection paths remain unconverted, and are worse than described: a failure there erases the whole transaction, not just its own audit row | → OPEN (narrowed scope) |
| GAP-045 | Unchanged, confirmed fixed in REVIEW-008 | CLOSED |
| GAP-046 | **FULLY FIXED (second attempt).** All three sub-defects independently re-reproduced: `EventBridge.handle()` raises on CAS failure (409, not 204); the losing event is no longer marked processed and is genuinely retryable; `atomic_transition()` no longer corrupts the in-memory object on failure | CLOSED |
| GAP-047 | **Schema/tests genuinely fixed and independently reproduced.** Authentication was not added, but this is consistent with the rest of the app (including `POST /approvals`) and already tracked as a Phase 2 candidate task in `docs/NEXT_STEPS.md` — not re-opened as its own item | CLOSED (auth deferred, tracked project-wide) |
| GAP-054 | **NOT ACTUALLY FIXED.** The `RUNNING`-CAS failure site still logs nothing; moot anyway since none of `_trigger_execution`'s audit rows survive a rollback | → OPEN |
| GAP-055 | **FULLY FIXED** for the cited assertion (exact `== 3`, confirmed to catch a double-CAS regression). The new companion test only achieves this via an unrealistic test-only `db.commit()` | CLOSED (with a caveat folded into GAP-044) |

## The one substantial finding: `_trigger_execution`'s failure paths lose more than an audit row

REVIEW-008 described GAP-044's remaining gap as "audit rows... lost." Direct reproduction this round shows
it's worse: because `_trigger_execution` never calls the new commit-before-raise helper, and its
`RuntimeError` (executor-start failure) isn't caught by `api/approvals.py` (which only handles `ValueError`),
the exception reaches `get_db()`'s rollback and erases the **entire transaction** for that request —
including the legitimate `EXEC_APPROVED` transition and `Execution` row that were correctly written earlier
in the *same* request, before the executor call failed. A human retrying the same approval afterward would
see no trace that anything had happened the first time — not just a missing audit explanation, but the
approval's own prior, valid progress silently undone. Reproduced 3 separate ways: the executor-crash
`RuntimeError` path, the `READY`-CAS concurrent-modification path, and the `FAILED`-CAS (GAP-054's target)
concurrent-modification path — all three lose everything on a fresh re-query after rollback.

## Recommendation

`GAP-044`/`GAP-054` need `_trigger_execution` to either use the same commit-before-raise pattern as
`approve()`'s top-level rejections, or have `api/approvals.py` catch `RuntimeError` alongside `ValueError`
and commit the failure state explicitly before returning an error response. Given this is the fourth attempt
at this general class of bug (session lifecycle vs. exception paths), a systematic fix — auditing every
`raise` in `approval_service.py` against whether it's covered by a commit-before-raise, rather than patching
one call site at a time — would likely be more reliable than another targeted patch.

## Verification

`cd src/governance_controller && uv run pytest -q` → 171 passed (unchanged; no application code modified by
this review). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors. Both agents'
reproductions drove real, unmodified session/generator objects exactly as FastAPI does, and for GAP-046 in
particular, drove the actual ASGI `POST /events` route rather than calling `EventBridge.handle()` directly.
