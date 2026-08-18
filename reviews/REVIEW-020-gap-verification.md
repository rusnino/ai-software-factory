# REVIEW-020: Verification of REVIEW-019's fixes (commit f62c5ba)

Date: 2026-08-19
Scope: live re-verification of the six gaps REVIEW-019 opened (`GAP-077`, `GAP-085`, `GAP-086`,
`GAP-087`, `GAP-090`, `GAP-091`), fixed in a single commit `f62c5ba`. Two parallel verification
agents: one dedicated to the highest-risk change (the rewritten retry mechanism, `GAP-077`/`GAP-085`),
one covering the other four. I personally re-confirmed the ledger-hygiene issue (dangling commit
hashes) both agents independently flagged, and reconciled the ledger myself given several rows had
been left self-contradictory by the coding agent's own edits.

## Baseline

`uv run pytest -q` → 209 passed (up from 202 — new tests for this round's fixes). `uv run ruff check .`
→ clean. `uv run mypy governance_controller` → 0 errors.

## Verdict: 5 of 6 gaps genuinely (or honestly-scoped) closed; `GAP-077` stays open on a narrower,
## now-more-severe residual; `GAP-091` reopened for two missed cases; one new ledger-hygiene finding.

### Genuinely fixed, independently re-verified

**`GAP-085`** (the retry never restarted execution) — **GENUINELY FIXED**. The new
`_start_retry_execution()` creates a real `Execution` row and calls `executor.start()`. Live-reproduced
the full round trip through `EventBridge.handle()` with a mocked executor: `start()` called exactly
once per retry, a new `Execution` row each time, and the exact `max_retries` boundary confirmed (2
retries then terminal `FAILED`, `executor.start()` called exactly twice). Also confirmed no
`GAP-080`-class regression despite this method now making its own outbound call: the state transition,
audit rows, and `Execution` row commit before `executor.start()` runs (a lock probe against real
Postgres returned in 0.02s, not blocked), and a forced two-session race showed exactly one racer
succeeding, no double-count.

**`GAP-077`'s first bug** (global transition-table scope) — **GENUINELY FIXED**. `state_machine.py`'s
shared table no longer contains `FAILED -> RUNNING` (`TaskState.FAILED: set()`); a dedicated
`atomic_transition_from_failed_to_running()` is the sole call site. Live-reproduced delivering all 6
previously-dangerous event types to a `FAILED` task through the real `EventBridge.handle()` — every
one correctly rejected (`Invalid transition: FAILED -> RUNNING`), task stays `FAILED`, each producing
a clear `transition_error` audit signal. No resurrection.

**`GAP-086`** (middleware drain-loop infinite spin) — **GENUINELY FIXED**, confirmed by a rigorous
before/after comparison: reverting just the 2-line fix and re-running the identical reproduction
showed `receive()` called over 1,000,000 times in 3 seconds (proving the pre-fix code truly loops
forever, not just "runs slow"); against the current code the same harness returns promptly with no
response sent.

**`GAP-087`** (delimiter-unsafe idempotency key) — **GENUINELY FIXED**. The fallback key is now a
`sha256` hash of a JSON-encoded tuple. Live-reproduced the exact original collision
(`task_id="myid|plan"`/`actor="bob"` vs. `task_id="myid"`/`actor="plan|bob"`) against both a reverted
copy (confirmed the collision and silent approval loss reproduce) and the current code (confirmed both
tasks now reach `PLAN_APPROVED` independently, verified via direct DB inspection, not just HTTP
responses).

**`GAP-090`** (SPEC-09 §9.6 "alert human" undisclosed/unimplemented) — closed on an honest basis.
`_alert_human_terminal_failure()` writes a real, tested `alert_human` audit row when retries exhaust.
This is **not an actual outbound alert** (confirmed via grep — no webhook/email/notification channel
exists anywhere), but the code's own docstring says so plainly, matching this project's established
pattern for honestly-scoped Phase 1 stubs (Plane adapter, CLI). A real notification channel remains
Phase 2 work, now explicit in `docs/NEXT_STEPS.md`.

### Still open

**`GAP-077`, narrowed** — the event-replay bug is **not fixed, and is now more severe than originally
reported**. `EventBridge`'s early-return still skips recording the processed-event dedup key on a
retry, so a replayed `landing:completed` event (identical `event_id`) re-enters the retry path. Before
`GAP-085`'s fix, this only double-incremented a bookkeeping counter with no real side effect. Now that
the retry path genuinely calls `executor.start()`, a replay causes a **second real live macro-agent
execution and a second real `Execution` row** — live-reproduced end to end through `EventBridge.handle()`.
No test exercises this path (`test_duplicate_event_is_idempotent` uses a task with no
`task_contract_json`, so it never enters the verification/retry branch at all). This checklist item in
`SPEC-10` (`Failed verification never produces DONE`) doesn't need to be unchecked over this — a
duplicate execution attempt isn't itself a `FAILED`/`DONE` boundary bypass — but the row stays open.

**`GAP-091`, reopened** — the fix added validators rejecting negative `pool_size`/`max_overflow` and
unrecognized `log_level` values (all confirmed working), but missed two cases named in the gap's own
original description: `pool_size=0` reproduces the identical unbounded-pool bug the fix was meant to
close (the validator only checks for strictly-negative values), and `pool_timeout` got no validator at
all — live-confirmed `GC_DATABASE_POOL_TIMEOUT=-30` is still silently accepted.

### New finding: recurring ledger-hygiene issue

**`GAP-092` (LOW, new)** — both verification agents independently noticed `reviews/GAPS.md`'s own
edits (made by the coding agent alongside its fix) cited `Closed By` hashes (`68589d6`, `384ed34`,
`d6adb79`) that are not reachable from `main`. I confirmed via `git cat-file -t` (all exist as raw
objects) and `git merge-base --is-ancestor <hash> HEAD` (none are ancestors) — these are dangling
sibling commits of the actually-merged `f62c5ba`, almost certainly leftover amend/rebase artifacts.
This is the same class of problem REVIEW-019 already found and fixed once (a literally nonexistent
hash, `9ae1357`) — recurring in a subtler form that `git log --all` alone won't catch (the objects
exist, they're just unreachable, and will become truly unfindable once `git gc` runs). All hashes in
this round's rows corrected to `f62c5ba` directly. Logged for the record; not a `governance_controller`
defect, a coding-agent-workflow hygiene note.

### Ledger self-contradictions found and corrected

Independent of the hash issue, several rows the coding agent edited were internally inconsistent —
marked `CLOSED` while their descriptive prose still said "this row stays open" (`GAP-090`) or described
unresolved regressions (`GAP-077`), and `GAP-087`'s row had literal unescaped pipe characters in its
prose that broke the Markdown table structure. All corrected directly in this review.

## Verification

`GAP-085` and both parts of `GAP-077` were independently confirmed by the dedicated verification agent
via live reproduction against the real `EventBridge.handle()` entry point and a real Postgres
container for the concurrency/lock checks. `GAP-086`/`087`/`090`/`091` were confirmed by the second
agent, including a genuine before/after comparison for `GAP-086` (reverting the fix to prove the
reproduction actually discriminates). The dangling-hash finding was personally re-verified via
`git cat-file`/`git merge-base`. No application or test code was modified — all changes this round are
to documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`, `specs/SPEC-10-phase-plan.md`.

## Milestone

The gate remains open, now on a single, narrow, well-understood residual (`GAP-077`'s event-replay
bug) plus the non-blocking `GAP-091`/`GAP-092`. This is real progress from REVIEW-019's four open HIGH
rows down to one.
