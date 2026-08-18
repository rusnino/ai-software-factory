# REVIEW-007: Full fresh codebase review, round 2

Date: 2026-08-18
Scope: the entire `src/governance_controller/` codebase after `REVIEW-006` confirmed no `CRITICAL`/`HIGH`
gaps remained open. Two parallel reviewers covered (1) the core governance/concurrency/audit path in light
of GAP-022/023/024/032's recent heavy changes, and (2) adapters/harness/tests/tooling/docs accuracy —
independently converging on the same central finding from two different angles, a strong cross-validation
signal.

## Headline finding

**`EventBridge.handle()` has no live HTTP entry point anywhere in the application, and this is not
disclosed** — `docs/NEXT_STEPS.md` and `specs/SPEC-10-phase-plan.md` both describe it as an operating,
integrated capability. This matters because (a) it's the only caller of `VerificationService.verify_and_advance`,
so all of GAP-006/021/026's hardening protects a call chain nothing can currently invoke, and (b) it reveals
a second, more serious problem: `EventBridge`'s state transitions still use the pre-GAP-023 unguarded
mutation pattern, so the moment Phase 2 wires a live webhook route to it, a real race against
`ApprovalService`'s new atomic compare-and-swap reactivates with zero additional code change needed to
trigger it. Both reviewers found this independently.

## New findings by severity

### HIGH

**GAP-044 — `get_db()`'s rollback erases the audit trail for every rejected approval, not just execution
failures.** Self-approval rejection, permission-denied, policy-violation, and the GAP-023
concurrent-modification guard all write an audit row immediately before raising — and that raise still
propagates through `get_db()`'s rollback, discarding the audit row along with everything else in the
transaction. Reproduced live: both the task and the "approval_rejected" audit entry vanish after a simulated
rejected request. The security decision itself is unaffected (the caller still gets the correct 403/409),
but SPEC-03 §3.8 ("every governance event stored") is violated for the entire class of rejected approvals —
the primary path, not an edge case.

**GAP-045 — `TaskContract.verification.commands` are policy-validated but never executed without a
`completion_contract`.** `VerificationService`'s no-`completion_contract` branch never touches
`verification.commands` at all; it reports hardcoded `"schema"`/`"syntax": "passed"` and only fails on a
self-reported `forbidden_paths` substring match. A contract following SPEC-03 §3.5's own canonical example
(`verification.commands` only) gets approved, then silently never runs its declared `pytest`/`ruff`/`mypy`
commands and advances to `HUMAN_REVIEW` on entirely fake checks.

**GAP-046 — GAP-023's atomic CAS discipline doesn't extend past the three approval-gated transitions.**
`_trigger_execution`'s `READY→RUNNING/FAILED`, `VerificationService.verify_and_advance`, and every
`EventBridge.handle()` transition still use the old unguarded `StateMachine.transition()` — plain mutation,
no version bump, no WHERE-clause guard. A concurrent `EventBridge`-driven event can silently clobber a
just-committed approval transition with no conflict error and no audit trail explaining the overwrite.
Currently dormant only because of GAP-047.

**GAP-047 — `EventBridge` is fully orphaned and this isn't disclosed.** See headline above.

**GAP-048 — `docker-compose.yml` doesn't actually work.** `DATABASE_URL`/`LOG_LEVEL` lack the `GC_` prefix
`Settings` requires, so both are silently ignored; the `api` container falls back to a default pointing at
`localhost`, which has no Postgres inside that container — `main.py`'s unconditional `init_db()` on startup
would crash it. The one documented quick-start path is broken. Confirmed empirically (env var set, `Settings`
read directly, showed the unmodified default).

### MEDIUM

**GAP-049 — role enforcement (GAP-032) is bypassed for any registered-in-profile-but-not-in-registry
harness** (e.g. `"aider"`, per SPEC-03's own example profile) — the role check only runs inside
`if registry.is_registered(...)`, so an unregistered harness skips validation entirely rather than
defaulting to deny.

**GAP-050 — `SPEC-10`/`README` still cite `GAP-020`/`021`/`022` as open**, stale since REVIEW-005/006 closed
them; conservative-direction staleness (doesn't overclaim completeness) but inaccurate, and the two
GAP-022-blocked checklist items should be re-evaluated now that the blocker is gone.

### LOW

Several `TaskContract`/`ProjectProfile` execution fields remain schema-only (GAP-051, same class as the
already-`ACCEPTED` GAP-039); `Task.version`'s DB-level default is ORM-only, no Alembic migrations exist,
harmless while every write goes through the ORM (GAP-052); minor doc/naming nits — `structlog`'s claimed
scope in `DEPENDENCIES.md` overstates its actual one-call-site usage, and two unrelated schema classes
share the name `ExecutionConfig` (GAP-053).

## Recommendation

Five new `HIGH` gaps reopen the "Phase 1 not done" state that `REVIEW-006` had just cleared. Per `AGENTS.md`'s
gate rule, `docs/NEXT_STEPS.md`'s "no CRITICAL/HIGH gaps remain open" claim is corrected again as part of
this review. Of the five, `GAP-048` (broken docker-compose) is the fastest to fix and highest-value for
anyone actually trying to run this locally; `GAP-047`+`GAP-046` should be considered together (wiring
EventBridge to a real route without first fixing its transition discipline would immediately activate a live
race); `GAP-044`/`GAP-045` are both instances of "the happy path works, the exception/absence path silently
discards something SPEC-03 requires" — the same category of bug as GAP-031, suggesting this class of defect
(state discarded on early-return) is worth a systematic sweep rather than one-off patching.

## Verification

`cd src/governance_controller && uv run pytest -q` → 156 passed (unchanged; no application code was
modified by this review). `uv run ruff check .` → clean. `uv run mypy governance_controller` → 0 errors.
GAP-044's reproduction drove the real, unmodified `get_db()` generator exactly as FastAPI does, not the test
suite's fixtures (which cannot exercise this path at all, per the same finding pattern as GAP-022/043).
