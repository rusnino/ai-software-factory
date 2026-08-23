# REVIEW-025: Verification of GAP-095/097's third fix attempt

Date: 2026-08-23
Scope: the two residual gaps left open after REVIEW-024 (`GAP-095`'s process-group timeout,
`GAP-097`'s uncommitted dedup-key write), fixed in commit `85a1fd8`. Not a full fresh review, per
explicit instruction — targeted verification only.

## Verdict: both GENUINELY FIXED

**GAP-095**: `_run_check` now spawns with `start_new_session=True` and kills the whole process group
(`os.killpg(..., SIGKILL)`) on timeout, instead of `proc.kill()` on just the shell's own PID.
Live-reproduced: `sleep 300`/`timeout=3.0` now returns in 3.00s with no leftover process, confirmed for
both a single-fork case and a nested `sh -c 'sleep 300'` double-fork case; a CPU-bound never-exiting
command also returns cleanly at its timeout. No regression to normal commands or to the earlier
row-lock fix (concurrent write still returns in ~0.04s).

**GAP-097**: `EventBridge.handle()` now commits immediately after writing the dedup key inside the
`finally` block, before the original exception can reach `get_db()`'s rollback. Live-reproduced through
the real ASGI app with the real, unmodified `get_db()`: the `ProcessedEvent` row now genuinely persists
despite a mocked `executor.start()` failure (confirmed via a separate DB connection), and redelivering
the identical event is now correctly deduplicated rather than reprocessed. Traced every raise site in
the retry path to confirm the new commit doesn't accidentally sweep in other unrelated pending state —
each already had its own preceding commit from the original fix. The full `max_retries=2` boundary
sequence still produces exactly 2 retries then terminal `FAILED`.

## Verification

Personally confirmed the cited commit hash (`85a1fd8`) is a genuine ancestor of `HEAD` via
`git merge-base --is-ancestor`. No application or test code was modified — only `reviews/GAPS.md` was
updated with this confirmation.

## Milestone

`reviews/GAPS.md` has zero `OPEN`/`IN_PROGRESS` rows again. This is the third fix attempt on this
specific pair of defects (`GAP-095`/`GAP-097` each went through two prior partial fixes) and the third
time this series has reached a clean gate — REVIEW-023 reopened it after REVIEW-022's first clean
milestone, and this round closes it again.
