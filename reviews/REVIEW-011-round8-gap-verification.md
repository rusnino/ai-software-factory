# REVIEW-011: Verification of REVIEW-010's GAP-056 fix

Date: 2026-08-18
Scope: `GAP-056` (the last remaining open gap after REVIEW-010) — a test-placement fix for the tests meant
to protect `_trigger_execution`'s three commit-before-raise sites (`GAP-044`/`GAP-054`).
Method: the empirical regression-detection proof REVIEW-010 itself used — but since the verifying agent has
no file-edit access, it reproduced "delete this one commit call" via frame-scoped monkeypatching of
`AsyncSession.commit` (a wrapper that no-ops the call only when the immediate caller frame matches the exact
target file:line, verified by hit-counters to fire only there) rather than touching the file on disk. Repo
confirmed clean (`git status`/`git diff`) before and after.

## Verdict: PARTIALLY FIXED — 1 of 3 sites genuinely protected, 2 of 3 still are not

Commit `3f45649` moved `pytest.raises(...)` to wrap the entire `async with get_db()` block (correct
mechanism) and removed a leftover manual `db.commit()` — a real, structural fix, not cosmetic. But applying
that mechanism to each of the three `_trigger_execution` sites individually:

| Site | Test(s) | Regression actually detected when the commit is removed? |
|---|---|---|
| Executor-crash / FAILED-CAS (line ~390) | `test_executor_failure_path_survives_get_db_rollback`, `test_executor_failure_advances_to_failed_with_version_3` | **Yes** — both flip red, task correctly left at `PLAN_APPROVED` instead of `FAILED` |
| READY-CAS-loss (line ~316) | `test_ready_cas_loss_survives_get_db_rollback` (by name) | **No** — 5/5 pass regardless; the test never touches this call site at all |
| RUNNING-CAS-loss (line ~413) | none | **No** — no test exists for this branch |

The most concerning finding: `test_ready_cas_loss_survives_get_db_rollback` isn't merely failing to cover
its named branch — it's silently testing a *different* commit site (`_log_rejection_and_raise`'s commit,
called from `approve()`'s own outer `PLAN_APPROVED→EXEC_APPROVED` CAS check, not `_trigger_execution`'s
internal `READY` CAS at all). Confirmed by removing that *other* commit instead and watching this exact test
fail. The test's own docstring says as much ("the loser fails at the PLAN_APPROVED→EXEC_APPROVED CAS in
`approve()` before `_trigger_execution` runs") — but its name still claims otherwise. This is precisely the
"reads correctly, tests the wrong thing" failure pattern that has recurred throughout this saga (GAP-021,
GAP-043, GAP-055's companion test, and now this).

## Recommendation

`GAP-056` needs two more things, not a rename or restructuring: a test that actually exercises
`_trigger_execution`'s internal READY-CAS-loss branch (line ~316) with the `pytest.raises`-wraps-`get_db`
pattern already proven to work at the executor-crash site, and a first test of any kind for the
RUNNING-CAS-loss branch (line ~413). Given this is the second attempt at GAP-056 specifically and the fourth
overall on this general area, worth checking systematically — for every `atomic_transition()`/CAS call site
in the file, is there a test that (a) forces that exact call to return `False`, and (b) would fail if the
commit immediately preceding its `raise` were removed — rather than adding tests one named branch at a time.

## Verification

`cd src/governance_controller && uv run pytest -q` → 173 passed (unchanged; no application or test code was
modified by this review — verification was done via in-process monkeypatching, confirmed not to touch disk).
`uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors. This does not reopen the
`CRITICAL`/`HIGH` gate (GAP-056 is `MEDIUM`) — `GAP-044`/`GAP-054`'s underlying code is still independently
confirmed correct from REVIEW-010; only the test safety net for two of its three branches remains absent.
