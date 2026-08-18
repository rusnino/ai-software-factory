# REVIEW-019: Verification of REVIEW-018's fixes + full fresh review

Date: 2026-08-19
Scope: (1) live re-verification of `GAP-077`/`GAP-084`'s fixes from commit `ee80cd6`, with maximum
skepticism given the fix implements a state-machine change (a historically fragile area) and this
project's coding agent operates under a skill ("ponytail") whose own rules prescribe minimal, narrow
regression tests; (2) a full fresh review of the codebase per the established per-cycle convention,
covering core services/state machine, adapters/API surface, and specs/docs/requirements conformance.
One verification agent plus three fresh-review agents; I personally re-verified the two most severe
individual claims (GAP-085's missing executor call, GAP-086/087's root code) directly from source
before writing this up.

## Baseline

`uv run pytest -q` → 202 passed. `uv run ruff check .` → clean. `uv run mypy governance_controller` →
0 errors. As with prior rounds, none of this round's findings are tooling-detectable — every one
required live reproduction or close reading, consistent with the pattern this whole series has
established.

## Verdict: GAP-084 GENUINELY FIXED. GAP-077 still not safe — a third, more severe bug found on top of
## the two already known. Two new, unrelated HIGH findings from the fresh sweep. Several MEDIUM
## doc-accuracy/hygiene items.

### GAP-084 — GENUINELY FIXED

The hardcoded `in_memory_get_db` fixture is removed from `tests/test_db.py`; all 6 previously-bypassing
tests now route through the shared `patched_db` fixture. Live-verified against a real Postgres
container: correct credentials → 202/202 pass; deliberately wrong credentials → exactly those 6 (and
no others) fail with a real `asyncpg.exceptions.InvalidPasswordError`; plain SQLite run → 202/202 pass.
Genuine dual-DB parity restored for the last known bypass.

### GAP-077 — still not genuinely fixed; a third, more fundamental bug found

The two regressions REVIEW-019's dispatch already anticipated (global transition-table scope; event
replay burning a retry) were both reconfirmed. But the core services review agent found something
more severe, which I personally verified by reading the code directly:

**GAP-085 (HIGH, new) — the "retry" never actually retries anything.** `verify_and_advance()`'s retry
branch (`verification_service.py:258-297`) transitions the task to `RUNNING`, increments
`execution_attempts`, and writes an audit row that reads like a real retry occurred — but never calls
`MacroAgentExecutor.start()`. I confirmed via `grep` that `executor.start(...)` has exactly one call
site anywhere in the codebase, `approval_service.py:358` inside `_trigger_execution`, reachable only
from the `EXEC_APPROVED -> READY -> RUNNING` approval flow — there is no transition-table edge back
into that flow from anywhere else. Live-reproduced twice (direct `verify_and_advance()` call, and
through the real `EventBridge.handle()` entry point with a genuinely failing `TaskContract`): the task
flips to `RUNNING`, the counter increments, the audit trail looks legitimate — but zero `Execution`
rows are created and zero outbound calls are made. The task is **permanently orphaned** in `RUNNING`:
no event will ever arrive to move it further (nothing is actually running to produce one), and no API
route can recover it (no state-machine edge or approval-flow re-entry exists back into `RUNNING` from
outside). This is worse than the pre-fix behavior — `FAILED` was at least terminal and human-actionable.

Net assessment of `GAP-077`'s current state: correct retry *arithmetic*, wired into a *transition* that
is both too broadly reachable (bug 1) and doesn't actually do the thing it represents (bug 3), with a
dedup gap on top (bug 2). All three stem from the same commit and are tracked together under `GAP-077`
(reopened) plus the newly-split-off `GAP-085`.

**Confirmed independently, with harder evidence than a description**: `git show ee80cd6` shows
`test_verification_service.py` has a **zero-line diff** despite `verification_service.py` gaining 46
lines of new retry logic in that same commit. `test_event_bridge.py`'s only change is adding
`execution_attempts=2` to one existing fixture (to preserve that pre-existing test's old expected
behavior, not to test the new path). No test anywhere exercises the actual `FAILED -> RUNNING`
transition, `atomic_transition_with_fields`, or any of the three bugs above.

### New, unrelated findings from the fresh sweep

**GAP-086 (HIGH)** — `WriteBodySizeLimitMiddleware`'s drain loop (added for `GAP-062`/`GAP-073`) only
recognizes `http.request` messages as terminating; if a client disconnects mid-stream after exceeding
the size cap (an ordinary occurrence, not an edge case), `receive()` returns a `disconnect` message
forever and the loop spins indefinitely — I confirmed this by reading the loop directly: its condition
`tail.get("type") == "http.request" and not tail.get("more_body", False)` can never be satisfied by a
disconnect message. Live-reproduced pegging a process at 99.9% CPU with no response ever sent. A
straightforward DoS via any of `/tasks`/`/approvals`/`/events`, invisible to the existing tests (all of
which use `httpx.AsyncClient`, which always finishes what it starts sending).

**GAP-087 (HIGH)** — `_make_idempotency_key()`'s fallback key (used whenever no `Idempotency-Key`
header is sent, which is every real caller today) joins `task_id`/`approval_type`/`actor`/`timestamp`
with `"|"` with no escaping — I confirmed this by reading the function directly. Two different
tasks/actors can collide on the delimiter and produce byte-identical keys. Live-reproduced: task
`"myid|plan"`/actor `"bob"` and task `"myid"`/actor `"plan|bob"` (same approval type, same rounded
timestamp) produce the same key; submitting the second approval returns `200 {"approved": true}` while
the actual target task never advances out of `PROPOSED` — a silent, invisible approval loss, directly
undermining the project's "durable human governance" goal.

### MEDIUM findings, mostly doc-accuracy — corrected directly this round

- **`GAP-088`**: `REQUIREMENTS.md`'s `FR-19` overclaimed a reviewer-agent that doesn't exist, with no
  caveat (unlike `IFR-02`/`IFR-05`/`RISK-14`'s prior corrections for the same class of drift).
  Annotated directly.
- **`GAP-089`**: `SPEC-10`'s `[^phase1-verification]` footnote (attached to the checked "Failed
  verification never produces DONE" item) carried no reference to `GAP-077`/`GAP-085` despite both
  directly threatening that exact guarantee. Footnote updated directly.
- **`GAP-090`**: SPEC-09 §9.6 step 3 ("alert human") is completely unimplemented and, unlike step 2
  (macro-agent feedback, already flagged via a `# TODO`), had no disclosure anywhere. Disclosure added
  to `docs/NEXT_STEPS.md`; the mechanism itself remains a real, open feature gap.
- **`GAP-091`**: `config.py`'s DB pool-size and log-level settings accept invalid values silently — a
  negative pool size is accepted and produces a real unbounded pool (Python's `queue.Queue` treats
  non-positive `maxsize` as unbounded), silently defeating `GAP-083`'s own protection; an unrecognized
  log level silently falls back to INFO. Live-reproduced both.
- **`RISK-18`** (new): added to `REQUIREMENTS.md` for the defect class `GAP-077`'s bug 1 belongs to —
  a fix correctly scoped for its own call site but not audited against every other consumer of a
  shared mechanism it touches (here, `StateMachine`'s global transition table).

### Ledger hygiene: two fabricated commit hashes found and corrected

`GAP-077`'s and `GAP-084`'s rows (as written by the coding agent alongside its own fix commit) cited a
`Closed By` hash `9ae1357` that does not exist anywhere in the repository's git history — confirmed via
`git log --oneline --all`. The real commit is `ee80cd6`. Both rows corrected; also served as a reminder
to verify cited commit hashes actually exist, not just that a hash-shaped string is present.

### Swept, no new findings

- Every edge in `StateMachine`'s transition table besides the already-flagged `FAILED -> RUNNING` is
  correctly scoped to the single call site that needs it — checked against all consumers
  (`atomic_transition`/`atomic_transition_with_fields` direct calls and `EventBridge`'s generic
  validation) edge by edge.
- `execution_attempts` cannot leak or double-count across tasks (a per-row column, CAS-guarded).
- `task_service.py`, `audit_service.py`, `permission_service.py`, `policy_engine.py`, `db.py` — no new
  issues on a fresh pass.
- Middleware boundary conditions (no body, exactly-at-cap body) behave correctly; the vulnerability is
  specific to the multi-message/streaming drain path.
- Telegram adapter, Plane adapter, CLI, harness registry, all API routes — no regressions since
  REVIEW-016/017; `execution_attempts` correctly not exposed via `TaskResponse`.
- Dependencies, README, SPEC-01/02/04/06/08 conformance, SPEC-09 §9.1-9.5/9.7 — no new drift.
- `grep -rn "ponytail:"` found only the already-accepted `GAP-072` marker; no new self-disclosed
  corner-cuts.

## Verification

`GAP-085` (the missing `executor.start()` call) and the root cause of `GAP-086`/`GAP-087` were all
personally re-confirmed by reading the exact source directly, not just trusting the reporting agents'
descriptions. `GAP-084` was confirmed via the reporting agent's real-Postgres-container reproduction,
consistent with this series' established rigor. No application or test code was modified by this
review — all changes are to documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`,
`specs/SPEC-10-phase-plan.md`, `requirements/REQUIREMENTS.md`.

## Milestone

The `CRITICAL`/`HIGH` gate is open again: `GAP-077`, `GAP-085`, `GAP-086`, `GAP-087` are all `OPEN`/
`HIGH`. This is the fourth time this gate has reopened on a genuinely new defect (never a repeat of a
previously-fixed one) across 19 rounds — the review process continues to hold up as intended.
