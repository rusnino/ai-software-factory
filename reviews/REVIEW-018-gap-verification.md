# REVIEW-018: Verification of REVIEW-017's fixes + dual-DB test coverage audit

Date: 2026-08-19
Scope: (1) live re-verification of the second `GAP-080` fix attempt plus `GAP-082`/`GAP-083`, with
maximum skepticism since `GAP-080` had already fooled one round of "looks fixed" verification;
(2) a dedicated, numeric audit of whether the dual-DB test infrastructure (`GC_TEST_DATABASE_URL`)
actually gives complete SQLite/PostgreSQL parity, per a direct user question. Two parallel reviewers
covered these; I read the actual code diff myself before dispatching them to confirm the fix's shape
matched what it claimed.

## Baseline

`uv run pytest -q` → 202 passed on SQLite. Same suite against a real Postgres container via
`GC_TEST_DATABASE_URL` → 202 passed. `ruff check .` clean. `mypy governance_controller` clean.

## Part 1 — GAP-080 (second attempt), GAP-082, GAP-083: all GENUINELY FIXED

**GAP-080** was reopened in REVIEW-017 because a first fix (removing an explicit
`get_by_id_for_update()`/`FOR UPDATE` call) didn't address the actual mechanism — the row lock was
still held via an uncommitted transaction spanning the live macro-agent HTTP call. Commit `1490371`'s
second attempt adds `await self.db.commit()` in `_trigger_execution` immediately after the `Execution`
row is flushed and before `executor.start()` is called.

Given this exact defect had already survived one "looks fixed" verification, I insisted on the same
live-reproduction method that caught the first failure, not the checked-in test (which only asserts
`AsyncSession.commit` was called before `executor.start()` — a weaker, structural check that wouldn't
have caught the first failed attempt either). The verifying agent built a fresh Postgres container,
mocked a 4-second-slow executor, and probed the task row with `SELECT ... FOR UPDATE` from a second
connection 0.5s in:

| Attempt | Probe block duration |
|---|---|
| Original code (pre-GAP-080) | ~3.5s (REVIEW-016) |
| First fix attempt (`584b989`) | ~3.5s (REVIEW-017 — fix didn't work) |
| Second fix attempt (`1490371`) | **0.020s, 0.020s, 0.021s (3 trials, REVIEW-018)** |

The row lock is genuinely released before the outbound call now. Also checked for a new durability
gap the mid-method commit could introduce: `AsyncSessionLocal`'s `expire_on_commit=False` means
`task`/`execution` stay valid in memory after the commit (no stale-object bug); a crash between the
commit and the final `RUNNING` transition leaves the task at a sane `READY` state with a real
`Execution` row, not corruption — an inherent dual-write ambiguity ("did macro-agent get the call?")
that already existed in the opposite direction before the fix, not a new failure class. The existing
`test_trigger_execution_ready_cas_loss_commits_before_raise`/`..._running_cas_loss...` tests already
exercise losing a CAS after this exact mid-method commit and pass against real Postgres too.

One test assertion was weakened in this fix (`test_approval_concurrency.py`'s specific
`"Concurrent modification detected"` string check replaced with a comment noting the loser may hit
either the in-memory state check or the CAS). Ran the file 5× on SQLite and 5× on real Postgres —
7/7 passed every time, no nondeterminism found. This is a legitimate, honest weakening (the row lock
release changes how the two racing calls interleave) rather than a masked regression; the actual
invariant (`sum(start_counts) == 1`) is still asserted and held in every trial.

**GAP-082**: `get_by_id_for_update()` is fully deleted; confirmed via whole-repo grep that it and
`.with_for_update()`/`FOR UPDATE` no longer appear in any `.py` file.

**GAP-083**: `Settings` gained `database_pool_size`/`database_max_overflow`/`database_pool_timeout`/
`database_pool_pre_ping`, wired into `create_async_engine(...)`. Verified genuinely functional, not
just declared: instantiated the engine with all four overridden via env vars and inspected
`engine.pool` directly — `size()`, `_max_overflow`, `_timeout`, `_pre_ping` all reflected the
overrides. One cosmetic naming nit: the env var is `GC_DATABASE_MAX_OVERFLOW`, not
`GC_DATABASE_POOL_MAX_OVERFLOW` — doesn't match the `GC_DATABASE_POOL_*` prefix the fix's own commit
message implies; harmless, noted for the record.

## Part 2 — Dual-DB test coverage audit: "full coverage on both databases" is an overstatement

Prompted directly by the user's question, not a routine check. Exact numbers:

- **202 tests collected**, identically under plain `pytest -q` (SQLite) and with `GC_TEST_DATABASE_URL`
  pointed at a real Postgres container — same pass count both ways.
- Pointing that env var at a Postgres URL with **deliberately wrong credentials**: **126 passed, 76
  errored** with real `asyncpg.exceptions.InvalidPasswordError` — this reproduces REVIEW-017's earlier
  spot-check exactly, confirming the general "not a silent fallback" claim for that subset.
- **But 6 of `tests/test_db.py`'s 12 tests bypass the shared fixture entirely**:
  `test_get_db_yields_session`, `test_get_db_commits`, `test_get_db_rolls_back_on_exception`,
  `test_get_db_rolls_back_on_generator_exit`, `test_get_db_commit_branch_requires_exception_catch`,
  `test_get_db_success_branch_requires_no_exception`. They use a locally-defined `in_memory_get_db`
  fixture that hardcodes `sqlite+aiosqlite:///:memory:` via its own `create_async_engine` call,
  never reading `GC_TEST_DATABASE_URL` — confirmed by observing these 6 still pass even under the
  bad-credentials run, proving they never attempt a real connection regardless of the env var.
- This matters more than a generic bypass because these specific 6 tests exist to cover `get_db()`'s
  commit/rollback branch logic — the exact historically fragile area behind `RISK-16`/`GAP-022`/
  `GAP-043`/`GAP-058` — and have therefore never exercised that logic against `asyncpg`'s real
  transaction semantics, only `aiosqlite`'s.
- Separately (not a bypass, noted for completeness): `test_health_endpoint.py`'s 2 tests use an
  `AsyncMock(spec=AsyncSession)` by design, testing response-mapping on a simulated DB failure rather
  than real connectivity — zero real-DB coverage on either dialect for that one endpoint, but an
  intentional test design, not an accidental gap.
- No `skipif`/`xfail`/dialect-conditional branching found anywhere else in `tests/`; the remaining
  ~118 tests are legitimately DB-independent (schemas, state machine, policy engine, adapters, CLI,
  config, harness registry) and correctly out of scope for dialect parity.
- No CI workflow exists anywhere in the repo (confirmed again — no `.github/workflows`, `.gitea`,
  `.woodpecker*`), so the Postgres path stays opt-in only, consistent with the already-recorded
  `RISK-17`/`GAP-081`.

**Logged as `GAP-084` (MEDIUM)**: fix the 6 bypassing tests to route through the shared
`GC_TEST_DATABASE_URL`-aware fixtures so `get_db()`'s commit/rollback logic gets genuine dual-DB
coverage, matching the other 76 DB-integration tests.

## Verification

`GAP-080`'s fix was verified with the identical live-reproduction method that caught its first failed
attempt (real Postgres, real timing measurement, not a unit-test proxy) — deliberately not trusting
the checked-in test alone, given this exact gap had already survived one prior "looks fixed" round.
`GAP-082`/`GAP-083` were confirmed via direct repo-wide grep and live engine instantiation,
respectively. The coverage audit numbers were independently cross-checked against REVIEW-017's
earlier spot-check (76 DB-touching test failures under bad credentials, matching exactly) rather than
taken fresh without a sanity check. No application or test code was modified — all changes this round
are to documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`.

## Milestone

All `CRITICAL`/`HIGH` gaps are closed; the gate is clean for the first time since REVIEW-016 reopened
it three rounds ago. Two `MEDIUM` rows remain open: `GAP-077` (SPEC-09 §9.6 retry, intentionally
deferred) and the new `GAP-084` (dual-DB test-coverage gap in `test_db.py`).
