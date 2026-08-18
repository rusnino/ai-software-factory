# REVIEW-017: Full fresh review of the Phase 1 Governance Controller

Date: 2026-08-18
Scope: independent live re-verification of REVIEW-016's five fixes (`GAP-073`, `GAP-074`, `GAP-078`,
`GAP-079`, `GAP-080`) against a real PostgreSQL container — not a diff read — plus a genuinely fresh
sweep for new issues across core services, adapters/API, and specs/docs/tests, on the explicit
instruction to add newly-found gaps even without gating on old ones being closed first. Three parallel
reviewers: (A) core services/models/concurrency, with its own real `postgres:16-alpine` container; (B)
adapters/API/security surface; (C) specs/docs/tests/dependencies/config conformance. I personally
re-verified the most consequential claim (that `GAP-080`'s fix doesn't actually hold) directly against
source before writing this up.

## Baseline

`uv run pytest -q` → 202 passed (up from REVIEW-016's 186 — the delta is REVIEW-016's own fix
commits' new tests). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors.
The same suite run with `GC_TEST_DATABASE_URL` pointed at a real Postgres container also passed (202,
slower — 14.6s vs 2.6s — consistent with genuine network round-trips). Pointing that env var at a
Postgres URL with deliberately wrong credentials made all 76 DB-touching tests fail with a real
`asyncpg.exceptions.InvalidPasswordError` while the 126 non-DB tests still passed — proof the dual-DB
test infrastructure genuinely attempts Postgres rather than silently falling back to SQLite.

## Verdict: 4 of 5 REVIEW-016 fixes GENUINELY FIXED; 1 (`GAP-080`) looked fixed but wasn't — reopened.
## Plus 3 new findings (1 MEDIUM, 2 LOW), none blocking beyond what's already open.

### Genuinely fixed, independently re-verified against live Postgres

**GAP-078 (CRITICAL)** — every datetime column now uses `DateTime(timezone=True)`. Live-verified:
`POST /tasks` through the real app returns `201`; a direct SQL query against the container confirms
`pg_typeof(created_at) = 'timestamp with time zone'` with a correctly-offset value.

**GAP-079 (HIGH)** — the project-profile TOCTOU race is fixed via dialect-aware
`INSERT ... ON CONFLICT DO NOTHING` + re-select. Live-verified by forcing a genuine race with an
`asyncio.Barrier(2)` across two independent asyncpg connections creating tasks with different
`task_id`s sharing one brand-new `project_id`: both succeeded, exactly one profile row was created,
audit logging (`project_profile_created` vs `_updated`) correctly reflects real rowcount/content diff.

**GAP-074 (HIGH)** — `TaskContract.forbidden_paths` is no longer dropped. `PolicyEngine.evaluate()`
now unions `profile.security.forbidden_paths` with `contract.forbidden_paths`; `VerificationService`
does the equivalent union of `contract.forbidden_paths` with the `CompletionContract`'s
`forbidden_path_check.paths`, checked unconditionally regardless of whether a `CompletionContract` is
present. Live-reproduced the original bypass now correctly rejected, and confirmed the union works in
both directions (a path declared only in one source is still caught).

**GAP-073 (MEDIUM)** — the body-size limit (`WriteBodySizeLimitMiddleware`) now covers
`/events`/`/tasks`/`/approvals` and counts actual bytes off the ASGI stream. Live-reproduced both
bypass angles (no `Content-Length` header at all, chunked; a lying small `Content-Length` while
streaming a large body) against all three routes — all now correctly return `413`.

### Not genuinely fixed — REOPENED

**GAP-080 (HIGH)** — the explicit `TaskService.get_by_id_for_update()` (`SELECT ... FOR UPDATE`) call
was removed from `api/approvals.py`, and that specific line-level anti-pattern is gone. But the
user-visible defect the gap actually describes — a concurrent approval attempt on the same task
blocking for the duration of the live macro-agent HTTP call, instead of failing fast the way the
codebase's CAS design intends — still fully reproduces, via a mechanism the fix didn't address. I
personally confirmed this by reading `approval_service.py::_trigger_execution` directly: it runs
`atomic_transition(self.db, task, TaskState.READY)` (an `UPDATE`), adds and flushes an `Execution`
row, and *then* calls `await self.executor.start(...)` — all inside the same still-open, uncommitted
session. No `await self.db.commit()` appears anywhere before that call; commits only happen in the
exception branches, after the call returns. On any real MVCC database, an uncommitted `UPDATE` holds
an ordinary row lock until commit, independent of whether `FOR UPDATE` was ever used. The reporting
agent live-reproduced this against real Postgres: a monkeypatched 4s-slow executor blocked a
concurrent `SELECT ... FOR UPDATE` probe from a second connection for ~3.5s — the same block
REVIEW-016 measured for the original explicit lock. The fix commit's own stated rationale ("provides
fail-fast concurrency protection without pinning a lock across the external HTTP call") is factually
inaccurate. No test in the suite — including the new dual-DB infrastructure — measures actual
lock-hold duration, so this escaped both the fix's own tests and REVIEW-016's own coverage
recommendation. **Reopened** with the correct fix shape: commit (or flush-and-commit) the `READY`
transition before invoking `executor.start()`, or restructure so the external call happens outside any
open write transaction.

### New findings

**GAP-081 (MEDIUM)** — no `RISK` row covered the dialect-parity defect class `GAP-078`/`079`/`080` all
belong to (SQLite tolerating what Postgres rejects), compounded by no CI existing anywhere in the repo
to run the new opt-in Postgres test path automatically — which is exactly how `GAP-080`'s first fix
attempt slipped through within the very same fix cycle that added that infrastructure. **Added
directly as `RISK-17`** in `requirements/REQUIREMENTS.md`.

**GAP-082 (LOW)** — `TaskService.get_by_id_for_update()` is now orphaned dead code (its only call site
was removed by the `GAP-080` fix attempt); already self-disclosed as deprecated in its own docstring,
so not a documentation-accuracy issue, just an outstanding "wire in or delete" decision (same pattern
as `GAP-008`/`029`/`063`).

**GAP-083 (LOW, speculative)** — `db.py`'s `create_async_engine(...)` sets no pool-size/timeout/
pre-ping settings, and `Settings` exposes no env var to configure any; meaningless for SQLite, but
matters for `asyncpg` in a long-lived container, especially now that `GAP-080` has shown transactions
can be held open across an external HTTP call. No proven failure yet — named for awareness now that
Postgres compatibility is the enforced target.

## Item-by-item sweep results (no new finding)

- **`.with_for_update()`** — confirmed via grep to appear nowhere else in the codebase now.
- **Enum/JSON dialect handling** — `Task.state`/`Approval.approval_type` generate native Postgres
  `ENUM` types (vs. SQLite `VARCHAR`); `*_json` columns use `postgresql.JSON` explicitly. Both
  live-tested against real Postgres with no defect found — noted as an asymmetry worth awareness, not
  a bug.
- **`event_bridge.py`'s `_record_processed_event`** — already had its own correct dialect-aware
  `ON CONFLICT DO NOTHING` pattern predating this round; no new issue.
- **Adapters/API fresh sweep** (Telegram, Plane adapter, CLI, harness registry, all routes, response
  schemas) — no regressions from the GAP-073/074 fixes, no response-header leaks, no-auth-by-design
  disclosure still consistent everywhere it should be.
- **Test-suite substance** — new test files (`test_task_service_race.py`, `test_body_size_middleware.py`)
  and the `isolated_db`/`patched_db` fixture refactor of existing files are both substantive; diffed
  directly, no weakened assertions found.
- **Dependencies, README, `docker-compose.yml`/`Dockerfile`** — three-way dependency cross-check clean,
  no new package introduced by the DB-test-infra work; README accurate; no additional SSL/encoding
  mismatch found beyond the pool-tuning gap (`GAP-083`) already noted.

## Verification

`GAP-080`'s reopening was personally confirmed by reading `approval_service.py::_trigger_execution`
directly and independently tracing the absence of any commit before the `executor.start()` call — not
just trusting the reporting agent's claim. `GAP-078`/`079`/`074`/`073` were confirmed via each
reporting agent's detailed, live-reproduction-backed reports (real Postgres containers created and
removed, real ASGI app requests, forced concurrency scenarios), consistent with this series'
established rigor. No application or test code was modified by this review — all changes are to
documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`, `specs/SPEC-10-phase-plan.md`,
`requirements/REQUIREMENTS.md`.

## Milestone note

Per `reviews/GAPS.md`'s gate rule, Phase 1 still cannot be declared complete — `GAP-080` (HIGH) is
open again. This is the fourth time this series' gate has closed and reopened (REVIEW-010/012,
REVIEW-013→014/015, REVIEW-016→017), each time on a genuinely new defect rather than a repeat of a
previously-fixed one — the review process is holding up as intended.
