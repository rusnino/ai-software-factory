# REVIEW-004: Full fresh codebase review

Date: 2026-08-18
Scope: the entire `src/governance_controller/` codebase as it stands after `REVIEW-001`, `REVIEW-002`, and
`REVIEW-003`'s gap closures — not just re-checking known items, but a fresh sweep for anything not yet
found. Three parallel reviewers covered (1) the core governance path (approvals, policy, state machine,
audit, models/schemas), (2) the adapters/integration layer (Plane, Telegram, macro-agent, harness registry,
git-cascade, opentasks), and (3) tests/tooling/docker/docs accuracy. Findings below are new — everything
already tracked as `GAP-001`..`GAP-021` is not repeated here.

## Headline finding

**`session.commit()` is never called anywhere in the codebase.** Every `Task`, `Approval`, `AuditLog`, and
`Execution` row created during a request is discarded the instant that request's DB session closes, against
real PostgreSQL. This was invisible to 134 passing tests because the test fixture (`tests/conftest.py`) hands
every request in a test run the *same, already-open* transaction — production's per-request
`AsyncSessionLocal()` (`db.py`) is never exercised by the suite at all. This is more severe than anything
found in the first three review rounds: it means the Governance Controller, as shipped, is not durable in
any deployment that uses real Postgres, contradicting SPEC-03 §3.1/§3.8 and `AGENTS.md`'s "Governance
Controller is authoritative" outright. Tracked as `GAP-022`, `CRITICAL`.

## New findings by severity

### CRITICAL

**GAP-022 — no `session.commit()` anywhere; nothing persists past one request.** See headline above.
Reproduced directly: a `Task` created and flushed in one `AsyncSessionLocal()` session is invisible to a
brand-new session opened immediately after, with no exception raised — the API returns a normal `200`/`201`
because SQLAlchemy's `expire_on_commit=False` keeps the in-memory object populated even though nothing
durable happened.

### HIGH

**GAP-023 — concurrent approvals race, can double-trigger macro-agent execution.** No row locking or
version column on `Task`. Two concurrent `POST /approvals` requests for the same task (different
idempotency keys, so `GAP-004`'s dedup fix doesn't catch them) can each independently pass policy/permission
checks and each call `MacroAgentExecutor.start()` — a real, non-transactional HTTP call. Combined with
GAP-022, the resulting duplicate state won't even leave a DB record behind, but the external side effect
(two live macro-agent runs for one task) still fires.

**GAP-024 — `PolicyEngine` never validates `CompletionContract.command` content; shell execution is
unmitigated.** `PolicyEngine.evaluate()` reads only `TaskContract.execution`'s self-declared boolean flags
(`destructive_shell`, `uses_docker_socket`, etc.) and `inputs`/`deliverables` paths — it never looks at
`contract.completion_contract` at all. `VerificationService._run_check()` then executes every
`required`/`optional` `Check.command` via `asyncio.create_subprocess_shell` with no allowlist and no
cross-check against SPEC-08 §8.7's forbidden operations. A contract can declare `destructive_shell: false`
(satisfying PolicyEngine) while its `completion_contract.required[].command` is `rm -rf ~` — nothing catches
this at approval time, and it runs unattended when `landing:completed` fires, with no human in the loop.
Separately, `TaskContract.verification` (mirroring SPEC-03 §3.5's `verification.commands`) is accepted by
the schema but read nowhere — a caller following the spec's own example YAML gets silent no-op behavior.
Added `RISK-14` to `requirements/REQUIREMENTS.md` for the tracking gap this represented.

**GAP-025 — Telegram adapter has no webhook auth and hardcodes `actor`.** `TelegramAdapter.process_update()`
takes a raw dict with no signature/secret-token/IP-allowlist verification, and sets
`actor="telegram-user"` — a literal string, never derived from the sender (`message["from"]` is never read).
Currently unrouted dead code (confirmed no route wires it up), but one FastAPI route away from being a
direct, unauthenticated, unattributable path to the authoritative approval endpoint — and would make
per-actor accountability (including the GAP-001 self-approval check) meaningless for this channel the moment
it's wired up.

**GAP-026 — `EventBridge` has no event-level idempotency/replay protection.** SPEC-05 §5.4 requires the
Controller endpoint be "idempotent on `(task_id, event_type, event_timestamp, event_id)`" because retries
with exponential backoff are the *expected* failure-recovery path, not an edge case. `EventBridge.handle()`
has no dedup keyed on event identity at all; the codebase's own `test_duplicate_event_is_idempotent` proves
this by asserting a replayed event produces **2** audit log rows for one logical event — the test's name
calls this "idempotent" while locking in exactly the violation the spec prohibits.

**GAP-027 — `docs/NEXT_STEPS.md` falsely claimed Phase 1 "complete and fully reviewed."** While `GAP-020`
(HIGH) was still `OPEN` from `REVIEW-003`, contradicting the ledger's own gate rule. Corrected directly as
part of this review (see below).

### MEDIUM (see `reviews/GAPS.md` for full list: GAP-028 through GAP-037)

Most notable: `mypy --strict` (configured, never run before this round) surfaces 38 errors, including a real
type-contract violation in `api/health.py`'s error path (GAP-028); `GitCascadeService` is orphaned dead code,
same pattern as the already-fixed reconciliation stub (GAP-029), and its branch-name validator permits a
leading `-`, a latent git-argument-injection gate weakness (GAP-030); the macro-agent executor's outbound
payload is missing the SPEC-05 §5.7 traceability metadata block that `EventBridge` depends on for inbound
correlation (GAP-031); `HarnessProvider.allowed_roles` is dead and mismatched with SPEC-06 §6.2 (GAP-032);
`test_task_api.py` is the one API test file that never exercises its 404 branch (GAP-033); the Docker image
runs as root (GAP-034). GAP-035, GAP-036, and GAP-037 (missing RISK row, stale SPEC-10 footnote, stale
README table) were documentation gaps fixed directly as part of this review.

### LOW (GAP-038 through GAP-042)

An unreachable, unguarded `HUMAN_REVIEW -> RUNNING` state transition not in SPEC-03's diagram (GAP-038);
`ProjectProfile.llm`/`audit` fields remain schema-only with nothing yet to check them against (GAP-039,
accepted); implicit httpx timeout on macro-agent calls (GAP-040); `.dockerignore` doesn't exclude
`.env`-shaped files, currently inert since no such file exists in the project (GAP-041, accepted);
`docker-compose.yml` exposes Postgres to the host, intentional for local dev (GAP-042, accepted).

## Direct documentation fixes made in this review (no application code touched)

1. `docs/NEXT_STEPS.md` — replaced the false "complete and fully reviewed" claim with an accurate summary
   pointing at this review and `GAP-022`.
2. `specs/SPEC-10-phase-plan.md` §10.1 — unchecked "Controller stores task independently of macro-agent" and
   "Task cannot execute without durable human approval" (both voided by GAP-022), added a top-of-section
   caveat, and corrected the `[^phase1-verification]` footnote to describe the current (post-GAP-006) wiring
   instead of the pre-fix state.
3. `README.md` — corrected the Repository Layout table's stale `(future)` framing for `src/`/`tests/`.
4. `requirements/REQUIREMENTS.md` — added `RISK-14` for the unmitigated shell-execution concern (GAP-024).
5. Recovered an unrelated, unrelated-to-this-codebase housekeeping item found along the way: an earlier
   session's uncommitted `docs/research-longhorizon-harness.md` + its `RISK-13` row had been silently
   shelved by a `git stash` at the very start of the Phase 1 work and never restored. Recreated the file and
   reconciled the stashed `REQUIREMENTS.md`/`NEXT_STEPS.md` lines into their current versions; dropped the
   now-redundant stash.

## Recommendation

`GAP-022` (CRITICAL) must close before anything else matters — it undermines the premise of every other
governance guarantee in this codebase. `GAP-023`..`GAP-026` (HIGH) should close before Phase 1 sign-off per
`AGENTS.md`'s gate rule. The MEDIUM/LOW items are real but not blocking in the same way.

## Verification

`cd src/governance_controller && uv run pytest -q` → 134 passed (unchanged; this review touched no
application code). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 38 errors (newly
surfaced by this review, not previously run — see GAP-028). The GAP-022 reproduction was run directly
against `db.py`'s real session machinery, not against the test fixtures (which mask the bug).
