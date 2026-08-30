# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, twelve review rounds have run. Rounds 1-8
(`#154`-`#258`) landed and hold up on re-verification. Round 9 found opencode's `#253`/`#254` fix
reused this project's single most recurring defect class — "blind write committed before a CAS
check, regardless of the CAS outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing `#262`/`#263`
(both CRITICAL). Round 10 found the same pattern a third time (`#266`) via a systematic CAS sweep,
plus `#267` (HIGH, event-level authorization). Round 11 verified round 10's fixes clean, then found
this project's most severe concurrency bug yet via a genuinely new angle (real concurrent HTTP-level
event delivery): `#271` (CRITICAL), genuinely concurrent `landing:completed` redelivery ran real
verification multiple times and destroyed the `#240` dedup marker, via a THIRD distinct manifestation
of the SAME identity-map-staleness defect class — `db.get()`/stale re-reads not reflecting a
concurrent session's committed writes. **Round 12 verified ALL THREE of Round 11's fixes
(`#271`-`#273`) are genuinely closed — the third consecutive round with zero reopens, confirmed via a
direct before/after live comparison on the exact pre-fix and post-fix commits (15 concurrent
duplicate deliveries: 15 verification runs and a destroyed marker before the fix, exactly 1
verification run and a surviving marker after). But the SAME identity-map-staleness defect class
(now confirmed present in three unrelated subsystems — `EventBridge`/`#271`, and now a fourth) struck
again: `#275` (MEDIUM) found `ReconciliationService._task_still_in_state`'s staleness guard is
provably dead code for the exact same reason — a `select()` re-read that returns the session's
already-loaded, stale Python object instead of re-querying, live-reproduced showing the guard reports
"still RUNNING" for a task a concurrent session had already moved to `HUMAN_REVIEW`. Blast radius is
lower than `#271`'s since reconciliation never writes back to Controller state, only Plane's
non-authoritative projection — but it also posts a misleading Plane comment falsely claiming to have
synced to the "authoritative" state.** A dedicated sweep of every OTHER dedup/idempotency mechanism in
the codebase — explicitly recommended after round 11 — found one more real bug: `#274` (HIGH), the
intake adapters' duplicate-submission guard has the same TOCTOU shape as `#271`/`#275` but a
DIFFERENT failure mode: the DB's own unique constraint correctly prevents any actual duplicate row
(no data corruption), but the resulting `IntegrityError` is never caught by the `except RuntimeError`
handlers guarding this path, so 8 of 15 concurrent duplicate submissions got a raw `500` instead of
the documented clean `409` in one live reproduction — defeating the retry-safety property the 409
exists for. The same sweep found the Plane webhook receiver CLEAN under identical real concurrent
load (its dedup check is also TOCTOU-shaped, but the actual state write is protected by a genuine
atomic CAS, unlike intake's plain `INSERT`). A dedicated connection-pool-exhaustion resilience check
(a genuinely new angle) found the Controller fails safely under real pool exhaustion — no leaked
tracebacks, no half-committed state, clean startup-failure behavior when Postgres is unreachable, and
`GET /health` genuinely pings the database rather than returning an unconditional 200 — but surfaced
one polish gap, `#276` (MEDIUM): a pool-checkout timeout falls through to a generic 500 instead of a
purpose-built 503. **Every round that tried a genuinely new angle has found something no prior
round's angles could have found, without exception across all twelve rounds so far. The
identity-map-staleness defect class (`db.get()`/non-`populate_existing` `select()` returning stale
session-cached objects instead of a fresh committed read) has now been found in two unrelated
subsystems in as many rounds (`EventBridge` in round 11, `ReconciliationService` in round 12) — any
other code that does a "re-read to check for staleness" pattern should be treated as suspect until
verified to use `populate_existing=True` or an equivalent fresh-read technique, the same way
`RISK-16`'s CAS pattern was generalized into a standing sweep after round 10.** Query the live issue
list before trusting anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **3 open issues (0 CRITICAL, 1 HIGH, 2 MEDIUM, 0 LOW)** — `#274` (HIGH — intake
duplicate-submission race returns raw 500s instead of clean 409s), `#275` (MEDIUM — reconciliation's
staleness guard is dead code, same defect class as `#271` but lower blast radius), `#276` (MEDIUM —
pool-timeout falls through to a generic 500 instead of 503). **This is the first round in this
project's history with zero open CRITICAL issues.** Every prior round's findings (`#151`-`#273`) are
closed and independently re-verified — the third consecutive round with none reopened.

Phase 1 architectural summary: command validation uses an explicit `argv[0]` allowlist plus
per-binary dangerous-construct checks. Known-resolved bypass classes include wrapper/interpreter
smuggling, forbidden-path text scanning, destructive flags on allowlisted binaries,
command-execution primitives such as `git -c`, `tar --to-command`, `find -exec`, and `sed` `s///e`,
container escape flags, and removal of unauditable network/package-manager tools (`curl`, `wget`,
`apt`, `apt-get`, `dpkg`).

Phase 2 architectural summary: real Plane CE HTTP client + authenticated webhook receiver with
workspace-member actor resolution + allowlist; Controller→Plane projection service wired into
`ApprovalService.approve()`; a macro-agent-facing HTTP client wired to a self-declared Python stand-in
service (real `macro-agent` package deferred to Phase 3 — see
`decisions/ADR-002-macro-agent-stub-vs-package.md`); an opentasks DAG materializer reading live
Plane dependencies with guarded cycle detection, size cap, and concurrent fetching; a reconciliation
service/CLI that reads Controller DB state and can fix Plane state divergences with staleness checks;
authenticated intake adapters (Telegram/Email/generic) using shared secrets or HMAC signatures,
with body-size caps, creating HTML-escaped Plane drafts; verification failure feedback to macro-agent
and terminal alerting; optional OPA policy backend that runs only after the embedded PolicyEngine
passes and receives a minimized, optionally bearer-token-authenticated input document.

Test status (2026-08-30): **414 passed / 5 skipped** on SQLite, **417 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (68 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. **CI is green** (`.github/workflows/ci.yml`, added by `#223`,
had failed on all 13 runs since 2026-08-26 until Round 11's CI-infra fix). Round 12 made no
production-code changes of its own beyond what opencode's `ccc8461` fix commit already covered, so
these numbers are unchanged. Green tests are still not evidence of correctness in this project beyond
"nothing crashes" — none of round 8's through round 12's findings (`#253`-`#276`), including all five
CRITICALs found across those rounds, were caught by this suite; they required killing a live process,
adversarially re-reviewing the immediately preceding round's own fix, or throwing genuinely
concurrent real HTTP load at a live server backed by real Postgres. `#271` and `#274`-`#275`
specifically are cases where the suite's own tests would not have caught them even in principle — the
bugs only manifest under genuinely overlapping requests, which the existing test suite does not
exercise for any of these endpoints.

Implemented components:

- FastAPI application with `POST /tasks`, `POST /approvals`, `POST /events`, `GET /health`, `GET /tasks/{id}`,
  `GET /executions/{id}`, and `GET /tasks/{task_id}/audit-log`.
- SQLModel async PostgreSQL models: `Task`, `Execution`, `Approval`, `AuditLog`, `ProcessedEvent`.
- Deterministic state machine covering `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
- Embedded Policy Engine validating task contracts, project profiles, harness allowlist,
  forbidden paths (with path-prefix matching), security posture, git settings, approval chain, and
  `CompletionContract` shell-command allowlisting.
- Append-only `AuditService` wired into task/profile creation, approvals, state transitions,
  execution starts, macro-agent events, and verification results.
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors
  (now also rejects `system:*` / `agent:*` structured identifiers as of `GAP-096`).
- `ApprovalService` as the single convergence point for all approvals, with `Idempotency-Key`
  header support and key-based deduplication.
- Plane Adapter interface + in-memory stub.
- Harness Provider Registry with OpenCode and Claude Code metadata, including per-harness `allowed_roles`
  aligned with SPEC-06 §6.2.
- macro-agent executor abstraction (`MacroAgentClient`, `MacroAgentExecutor`) and `Execution` model,
  with explicit configurable HTTP timeout and outbound traceability metadata per SPEC-05 §5.7.
- Automatic execution trigger after `EXECUTION` approval, with `READY → RUNNING` transition.
- In-process macro-agent Event Bridge translating workspace events into Controller state updates;
  `landing:completed` triggers `VerificationService` and gates `AGENT_REVIEW → HUMAN_REVIEW`, with
  event-level idempotency.
- Dockerfile and Docker Compose for local API + PostgreSQL; image runs as non-root `controller` user.
- CLI (`approve`) and Telegram adapter stubs converging on `POST /approvals`.
- Verification service executes `CompletionContract`/`TaskContract.verification` `required`/`optional`
  shell commands, compares exit codes, and performs forbidden-path/scope checks.
- End-to-end Phase 1 smoke test.

*(Phase 1-scoped list above, kept as originally written. Phase 2's additions — real Plane client/
webhook/projection, macro-agent service stand-in, opentasks materializer, reconciliation, intake
adapter, OPA backend — are described in "Current State" above rather than itemized here, per the
same anti-staleness reasoning: itemized component lists in this file have gone stale repeatedly.)*

## Phase 1 Stop Conditions

None declared. OpenCode integration remains a stub path; no ACP/MCP blocker was encountered.

## Phase 2 Status

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Twelve review
rounds have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`),
Round 4 (fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`), Round 6 (adversarial review
of rounds 4-6's own new code, GET-endpoint auth audit, concurrency sweep, fresh end-to-end pipeline,
`#236`-`#242`), Round 7 (macro-agent adversarial testing, cross-project isolation, resource
limits/rate limiting, secret-leakage audit, `#243`-`#249`), Round 8 (adversarial review of round 7's
own fixes, systematic project-scoping sweep, Controller crash/restart resilience, unconstrained
schema fields, `#250`-`#258`), Round 9 (verification of round 8's fixes, adversarial review of round
8's OWN new poller code, Docker/dependency security, macro-agent-side restart resilience, database-
failure resilience, `#259`-`#265`), Round 10 (verification of round 9's fixes, a systematic sweep of
every OTHER `StateMachine.atomic_transition` call site for the `RISK-16` pattern, a dedicated audit-
log integrity/completeness review, an event-authorization/BLOCKED-unblock-spoofing review, and real
concurrent-approval racing against a live server, `#266`-`#270`), Round 11 (verification of round
10's fixes, a sweep of every OTHER event type's authorization boundary, real concurrent event-
delivery racing against a live server, and an OPA fail-behavior/secret-hygiene sweep, `#271`-`#273`;
this round also diagnosed and fixed a real CI infrastructure bug unrelated to any filed issue), Round
12 (verification of round 11's fixes with a direct pre-fix/post-fix comparison, a sweep of every
OTHER dedup/idempotency mechanism in the codebase, a dedicated connection-pool-exhaustion resilience
check, and a reconciliation-service concurrent-drift review, `#274`-`#276`). **3 issues remain open,
0 CRITICAL, 1 HIGH — the first round in this project's history with no open CRITICAL issue.** Round
12 verified all three of round 11's fixes are genuinely closed — the third consecutive round with
none reopened, this time confirmed with a direct side-by-side reproduction on the exact pre-fix and
post-fix commits. The recommended sweep of every other dedup/idempotency mechanism paid off exactly
as round 11 predicted: `#274` (HIGH), the intake adapters' duplicate-submission guard has the same
TOCTOU shape `#271` had, though a DB unique constraint prevents actual data duplication — the bug is
that the resulting `IntegrityError` isn't caught, so 8 of 15 concurrent duplicates got a raw 500
instead of the documented clean 409 in one live reproduction. The Plane webhook receiver, checked by
the same sweep, came back CLEAN — its dedup check is also TOCTOU-shaped, but the actual state write
is protected by a genuine atomic CAS, unlike intake's plain INSERT. Separately, `#271`'s root-cause
pattern (a "re-read to check for staleness" that returns a stale session-cached object instead of a
fresh committed read) recurred in a fourth, unrelated subsystem: `#275` (MEDIUM),
`ReconciliationService._task_still_in_state` has the identical staleness-guard-is-dead-code bug,
live-reproduced, though capped at MEDIUM since reconciliation never writes back to Controller state.
**This identity-map-staleness defect class has now been found in two unrelated subsystems in as many
rounds and should be treated as a standing sweep target going forward, the same way `RISK-16`'s CAS
pattern was after round 10.** A dedicated connection-pool-exhaustion resilience check (genuinely new
angle) found the Controller resilient overall — no leaked tracebacks, no half-committed state, a
real DB ping in `GET /health` — with one MEDIUM polish gap, `#276` (pool-timeout falls through to a
generic 500 instead of a purpose-built 503). **Do not treat "Phase 2 review" as bounded to Phase 2
code, to any fixed set of angles, or to code the current round didn't itself touch** — every round
that tried a genuinely new angle found something the previous rounds' angles couldn't have found,
without exception across all twelve rounds so far. The next round should keep trying new angles.

## Immediate Next Step: Fix the Intake TOCTOU, Then the Two MEDIUMs, Then Verify Gate-Clean

Prioritize `#274` first (the only open HIGH — concurrent duplicate intake submissions get a raw 500
instead of the documented clean 409, because `IntegrityError` isn't caught alongside the existing
`RuntimeError` handling; fix by catching it around the `IntakeSubmission` insert and translating to
409, matching the pattern that already protects the Plane webhook receiver's equivalent path). Then
`#275` (MEDIUM — fix `ReconciliationService`'s staleness re-read to use
`populate_existing=True`/`session.refresh()`, matching the fix already applied to `EventBridge`'s
equivalent check for `#271`) and `#276` (MEDIUM — map `sqlalchemy.exc.TimeoutError` to a 503, not a
generic 500). Before declaring Phase 2 gate-clean, run a fresh live-reproduction review and confirm
the live issue list has no open `severity:critical` or `severity:high` issues — and try an angle no
prior round has tried yet, given the track record above. Two concrete leads for the next round: (1)
a systematic sweep for the identity-map-staleness pattern (`db.get()` or a bare `select()` used to
check for concurrent changes without `populate_existing`/`refresh()`) across the rest of the
codebase, the same way round 10 swept for `RISK-16`; (2) no round has yet raced the CLI commands
(`approve`, `reconcile`, `poll-stuck-executions`) against each other or against concurrent API
traffic — e.g. a human running `approve` via CLI at the same instant as a `POST /approvals` call for
the same task.

Once verified gate-clean, Phase 3 scope (from SPEC-10 §10.3) is:

- Docker sandboxing for verification/execution. See
  `docs/research-verification-sandboxing-scope-2026-08-24.md`.
- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Advanced conflict recovery.
- Project Profiles per repo.
- Semantic Reviewer.
- Better Completion Contract.

Before starting Phase 3, confirm the live issue list has no open `severity:critical` or
`severity:high` issues.

### Carried-forward Phase 2 deferred work

- **Durable execution**: evaluate Temporal or Celery for retry/collect workflows; persist the
  runtime task graph from the opentasks materializer beyond in-memory construction.
- **Meta Orchestrator (OpenCode + BMAD + OpenSpec)**: the intake→idea-ingestion path exists, but
  the actual decomposition/planning engine doesn't. Evaluate sudocode-ai/sudocode's Spec/Issue graph
  model before building this from scratch — see `docs/research-alexngai-ecosystem-and-sudocode.md`.
- **Real `macro-agent@latest` integration**: still a self-declared Python stand-in, not the actual
  npm package — see `decisions/ADR-002-macro-agent-stub-vs-package.md` and `#151`.

## Blockers to Watch

- macro-agent API stability and `/runs` contract.
- OpenCode ACP compatibility with macro-agent MCP tools.
- Plane CE self-hosted availability and API rate limits.

## Deferred to Phase 3+

- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Semantic Reviewer.
- Production hardening (metrics, tracing, HA).
- Evaluate LongHorizon-Harness (or similar durable-execution wrappers) as an optional `AgentHarness`
  adapter for long-running/GUI-touching opentasks — see `docs/research-longhorizon-harness.md`.
- If macro-agent's pre-1.0 risk (RISK-02/08) ever materializes into a real blocker, alexngai/openswarm is a
  concrete alternative execution engine (untested API surface, verify before evaluating further);
  alexngai/openhive is a multi-swarm federation candidate once single-swarm operation is proven — see
  `docs/research-alexngai-ecosystem-and-sudocode.md`.
- Same macro-agent-alternative scenario: Untrivial-ai/agent-orchestrator (fleet manager for coding-agent
  CLI sessions, worktree-per-task, pluggable agent/runtime/SCM adapters, ~9.9k stars, very active) is a
  second concrete candidate — its Kanban UI would need to stay out of scope (Plane already owns that
  role) and its programmatic API surface is unverified — see
  `docs/research-agent-orchestration-and-governance-survey-2026-08.md`.
- microsoft/agent-governance-toolkit (tool-call-level policy middleware, potentially complementary to the
  Governance Controller rather than competing with it) is a watch item only, not adopted and not formally
  risk-tracked — its maturity signals (6,091 stars on a ~5-6 month old repo, "Public Preview" versioning)
  don't hold up to a first pass of scrutiny; revisit only if independently corroborated beyond GitHub's
  own counters — see `docs/research-agent-orchestration-and-governance-survey-2026-08.md`.
