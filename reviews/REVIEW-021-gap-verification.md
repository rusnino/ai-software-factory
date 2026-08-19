# REVIEW-021: Verification of REVIEW-020's fixes (commit da20f66) + accidental new finding

Date: 2026-08-19
Scope: live re-verification of the two gaps REVIEW-020 left open (`GAP-077`'s narrowed event-replay
bug, `GAP-091`'s missed `pool_size=0`/`pool_timeout` cases), fixed in commit `da20f66` — the third fix
attempt touching the retry/event-replay area. One dispatched verification agent covered both; I then
personally attempted to resolve the residual `GAP-093` (a suspected concurrency race the agent flagged
as unverified/theoretical) and, in the process of setting up that reproduction, discovered a new,
unrelated HIGH regression.

## Baseline

`uv run pytest -q` → 212 passed. `uv run ruff check .` → clean. `uv run mypy governance_controller` →
0 errors.

## Part 1 — GAP-077 and GAP-091: both GENUINELY FIXED

**GAP-077**: `_record_processed_event` was moved to run before the retry-path early-return, so the
dedup key is now persisted regardless of whether the event resulted in a retry. Live-reproduced: one
`landing:completed` delivery against a failing contract → retry (attempts=1, one `Execution` row, one
`executor.start()` call). Replaying the identical event → no change (still attempts=1, still one
`executor.start()` call) — the replay is correctly ignored at the dedup check before reaching
verification logic at all. Regression check: a genuinely distinct subsequent event (different
`event_id`, simulating a real re-landing) still processes normally and reaches `HUMAN_REVIEW` — the
fix doesn't over-block legitimate new events. Full `max_retries=2` boundary sequence (3 distinct
events against a permanently-failing contract) still produces exactly 2 `executor.start()` calls with
a correct terminal `FAILED` + `alert_human` audit row, and replaying all 3 historical events afterward
produces no further calls.

**GAP-091**: `pool_size`/`max_overflow` validators now reject `0` in addition to negative values; a
new `pool_timeout` validator rejects negative values while correctly still accepting `0` (a legitimate
SQLAlchemy "fail immediately rather than wait" configuration, semantically distinct from the
`pool_size`/`max_overflow` zero-is-unbounded bug this gap exists to prevent). Live-reproduced all four
combinations (0/negative for pool_size/max_overflow, negative/zero for pool_timeout, plus legitimate
positive values for all three).

## Part 2 — GAP-093: analyzed and found not exploitable

REVIEW-020's dispatch flagged, as a residual note outside its requested scope, that two genuinely
*concurrent* deliveries of an identical event might both pass `EventBridge.handle()`'s dedup `SELECT`
before either commits, potentially both reaching the retry/verification logic. I traced the actual
code path rather than accepting this at face value: both concurrent deliveries also independently
attempt the same `atomic_transition(RUNNING -> AGENT_REVIEW)` CAS before either can reach
`verify_and_advance()` — no commit happens in between. The second delivery's `UPDATE` blocks on the
real MVCC row lock held by the first's uncommitted transaction; once the first commits, the second's
`WHERE version=?/state=?` clause fails to match the now-advanced row, `atomic_transition` returns
`False`, and `EventBridge.handle()` raises `ValueError: Concurrent modification detected` — before ever
reaching the verification/retry code. The existing state-transition CAS already fully serializes
concurrent identical events. **Accepted, no code change needed.**

## Part 3 — GAP-094 (HIGH, new): the SQLite runtime path cannot start at all

While setting up a live reproduction for `GAP-093`, a standalone script importing `governance_controller.db`
with `GC_DATABASE_URL=sqlite+aiosqlite:///:memory:` crashed immediately:
```
TypeError: Invalid argument(s) 'pool_size','max_overflow','pool_timeout' sent to create_engine(),
using configuration SQLiteDialect_aiosqlite/StaticPool/Engine.
```
Confirmed with a minimal, clean repro (`GC_DATABASE_URL=... python3 -c "import governance_controller.db"`).
Root cause: `db.py`'s module-level `engine = create_async_engine(...)` passes `pool_size`/
`max_overflow`/`pool_timeout`/`pool_pre_ping` (added by `GAP-083`/`GAP-091`) unconditionally, but
SQLAlchemy's SQLite dialect uses a `StaticPool` that doesn't accept the first three at all. This means
the module fails to import, so nothing built on top of it — the CLI, any script, or `uvicorn
governance_controller.main:app` — can start under this configuration.

This matters because SQLite is not a hypothetical or deprecated path: `get_db()`'s own code explicitly
branches on `settings.database_url.startswith("sqlite")`, and this entire 21-round review series has
repeatedly relied on a "SQLite fallback path" for local dev without Postgres/Docker (explicitly named
in multiple prior REVIEW-*.md files). `GAP-083`'s fix broke this path entirely, and neither `GAP-083`
nor `GAP-091`'s own verification caught it — both tested the pool-setting *validation* logic and
engine instantiation against Postgres-dialect URLs, never against a real SQLite `database_url`. The
dual-DB pytest infrastructure (`GC_TEST_DATABASE_URL`) doesn't exercise this either, since its fixtures
construct their own engines directly rather than importing `db.py`'s module-level one. This is exactly
the `RISK-18` defect class this project already tracks (a fix correct for its intended dialect, not
audited against the other one the same module has to support).

Fix shape: only pass the pool-tuning kwargs when the dialect actually supports them (e.g., skip them
entirely for `sqlite`, since `StaticPool` doesn't have a meaningful pool size to tune).

## Verification

`GAP-077`/`GAP-091` were confirmed by the dispatched verification agent's live reproduction against a
real `EventBridge.handle()` and real `Settings()` instantiation. `GAP-093`'s non-exploitability was
established by personally tracing the code path (not a live reproduction, but a structural argument
resting on the already-verified behavior of `atomic_transition`'s CAS from prior rounds). `GAP-094`
was personally discovered and confirmed with a minimal, clean, reproducible import failure. No
application or test code was modified — all changes this round are to documentation/ledger files:
`reviews/GAPS.md`, `docs/NEXT_STEPS.md`.

## Milestone

Real progress: REVIEW-020's four HIGH rows are down to zero of the *originally* tracked ones (`GAP-077`
genuinely closed this round). But a fresh, unrelated HIGH gap (`GAP-094`) reopens the gate — the fifth
time in this series the gate has closed and reopened, each time on a genuinely new defect. Given
`GAP-094` breaks module import entirely under a documented configuration, it should be a fast,
low-risk fix (add a dialect check before passing the pool kwargs) — likely the last item before this
gate can stay clean.
