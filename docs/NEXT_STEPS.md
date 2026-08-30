# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, fourteen review rounds have run. Rounds 1-8
(`#154`-`#258`) landed and hold up on re-verification. Round 9 found opencode's `#253`/`#254` fix
reused this project's single most recurring defect class — "blind write committed before a CAS
check, regardless of the CAS outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing `#262`/`#263`
(both CRITICAL). Round 10 found the same pattern a third time (`#266`), plus `#267` (HIGH,
event-level authorization). Round 11 found `#271` (CRITICAL) via a different recurring defect
class: `db.get()`/non-`populate_existing` re-reads silently returning a session's stale,
already-loaded object instead of the true committed state (`RISK-19`). Rounds 12-13 found this
same staleness pattern twice more (`#275`, `#278` — the latter revealing round 6's own `#242` fix had
silently never worked), plus `#274` (HIGH, intake TOCTOU) and `#277` (HIGH — the documented `approve`
CLI command had never once been able to authenticate against any real deployment, invisible because
its own tests only mocked `httpx.post`). **Round 14 confirmed all three of Round 13's fixes
(`#277`-`#279`) are genuinely closed. This round's explicit brief — audit regression-test QUALITY,
not just presence, per the new Regression Coverage Policy — immediately found a real gap: opencode's
own `#277` fix commit only strengthened the existing MOCKED `httpx.post` test (asserting a headers
dict gets built) rather than adding a live-server check, which would have missed a future regression
exactly the way the original bug went undetected. Fixed directly this round with a genuine
subprocess-driven live-server test. Two genuinely new angles then found this project's worst finding
yet and a second high-severity architectural recurrence: `#280` (CRITICAL) — the `AuditLog` hash
chain, this project's core tamper-evidence guarantee, SILENTLY FORKS under real concurrent
`AuditService.log()` calls (a realistic scenario — multiple approval/event/poller code paths writing
audit entries for the same task around the same time). 20 genuinely concurrent calls produced a
3-way-forked chain with ZERO exceptions raised, live-reproduced and independently confirmed. The
`SELECT ... ORDER BY id DESC LIMIT 1 FOR UPDATE` tip-lookup — the exact mechanism whose code comment
claims "concurrent transactions serialize on the previous row rather than forking the chain" — is a
known Postgres anti-pattern (no `WHERE` clause) that doesn't actually serialize anything: 20
concurrent calls took barely longer than 1. A related `#281` (MEDIUM) found `compute_hash()` can't
even be called on a row loaded via `SELECT` (only on freshly-constructed instances), so this project
currently has no working tool to verify the chain's integrity after the fact either. Separately,
`#282` (HIGH) found `gc reconcile` — a command whose entire job is comparing Controller state against
Plane — has NEVER actually matched tasks against Plane issues correctly for realistic deployments: it
looks up Plane issues by `Task.id`, never `Task.plane_issue_id`, an EXACT recurrence of `#201`'s
defect class (fixed on the write path in `8739a0b`, a fix that never touched `reconciliation_service.py`
or `cli.py`) — live-reproduced with a real, unmocked HTTP fake-Plane server showing a genuinely
diverging, genuinely existing Plane issue misreported as "not found in Plane" and never fixed.**
**Every round that tried a genuinely new angle has found something no prior round's angles could
have found, without exception across all fourteen rounds so far. Two lessons now firmly established:
(1) the identity-map-staleness (`RISK-19`) and write-before-CAS (`RISK-16`) patterns are standing
sweep targets — five and three confirmed instances respectively, across unrelated subsystems; (2) a
documented feature or guarantee (a CLI command, a tamper-evidence chain, a reconciliation tool) can
be completely non-functional for many rounds while every existing test passes, because the tests
only ever exercised the mocked/identical-key/no-real-concurrency case — any future round should
specifically hunt for tests that assert a property using a fixture shape that happens to sidestep the
realistic case (e.g. Controller `task_id` == Plane issue `id` in test fixtures, which is never true
in production).** Query the live issue list before trusting anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **3 open issues (1 CRITICAL, 1 HIGH, 1 MEDIUM, 0 LOW)** — `#280` (CRITICAL — the
audit log's hash chain silently forks under real concurrent writes, defeating its own tamper-evidence
purpose), `#282` (HIGH — `gc reconcile` never matches tasks against Plane correctly for realistic
deployments, a recurrence of `#201`'s defect class), `#281` (MEDIUM — `compute_hash()` can't verify
an already-persisted row, so there's no working integrity-check tool for `#280`'s own chain either).
Every prior round's findings (`#151`-`#279`) are closed and independently re-verified.

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

Test status (2026-08-30): **429 passed / 8 skipped** on SQLite, **435 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (68 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. **CI is green** (`.github/workflows/ci.yml`, added by `#223`,
had failed on all 13 runs since 2026-08-26 until Round 11's CI-infra fix). The test counts jumped
because Round 13 also included a one-time regression-test backfill (12 tests for previously-untested
severity:high/critical fixes, commit `fd8ce39`) and Round 14 added one more (a live-server test for
`#277`, replacing a weak mocked one, commit `72be021`) — see CLAUDE.md's Regression Coverage Policy.
Green tests are still not evidence of correctness in this project beyond "nothing crashes" — none of
round 8's through round 14's findings (`#253`-`#282`), including all six CRITICALs found across those
rounds, were caught by this suite before their respective fixes landed; they required killing a live
process, adversarially re-reviewing the immediately preceding round's own fix, throwing genuinely
concurrent real HTTP/DB load at a live server or real Postgres, or — for `#277`/`#280`/`#282` —
simply trying to actually exercise a documented workflow (a CLI command, a concurrent-writer scenario,
a realistic non-identical task-id-vs-Plane-issue-id pairing) that every existing test's fixture shape
happened to sidestep.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Fourteen review
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
check, and a reconciliation-service concurrent-drift review, `#274`-`#276`), Round 13 (verification
of round 12's fixes, a systematic sweep for the identity-map-staleness pattern across the rest of the
codebase, and racing the CLI against live HTTP API traffic for the first time, `#277`-`#279`; this
round also did a one-time regression-test backfill for 12 previously-untested severity:high/critical
fixes from rounds 8-13), Round 14 (verification of round 13's fixes, a regression-TEST-QUALITY audit
per the new Regression Coverage Policy, a live end-to-end check of the `reconcile` CLI command, a
`macro_agent_service`-specific `RISK-16`/`RISK-19` sweep, and a real-concurrency stress test of the
audit log's own hash chain, `#280`-`#282`). **3 issues remain open, 1 CRITICAL, 1 HIGH.** Round 14
confirmed all three of round 13's fixes are genuinely closed. The test-quality audit immediately
found opencode's own `#277` fix commit had only strengthened a MOCKED test, not added a live-server
check — fixed directly this round with a genuine subprocess-driven test. Two genuinely new angles
then found this project's most severe finding yet: `#280` (CRITICAL) — the `AuditLog` hash chain,
this project's core tamper-evidence guarantee, SILENTLY FORKS under real concurrent
`AuditService.log()` calls, a realistic scenario this project's own approval/event/poller code paths
produce constantly. 20 genuinely concurrent calls forked into 3 branches with zero exceptions raised;
the `FOR UPDATE` tip-lookup that's supposed to prevent this is a known Postgres `ORDER BY`/`LIMIT`
anti-pattern that doesn't actually serialize anything (20 concurrent calls took barely longer than
1). A related `#281` (MEDIUM) found there is currently no working tool to verify the chain's
integrity after the fact either — `compute_hash()` can't be called on a row loaded via `SELECT`.
Separately, `#282` (HIGH) found `gc reconcile` has never correctly matched Controller tasks against
Plane issues for realistic deployments — an EXACT recurrence of `#201`'s defect class (`Task.id`
assumed equal to Plane's issue UUID), in a code path `#201`'s own fix (`8739a0b`) never touched,
live-reproduced with a real, unmocked fake-Plane HTTP server. **Two lessons are now firmly
established across fourteen rounds: (1) `RISK-16` (write before CAS, committed regardless) and
`RISK-19` (identity-map staleness) are standing sweep targets — three and five confirmed instances
respectively, across unrelated subsystems; (2) a documented feature or guarantee can be completely
broken for many rounds while every existing test passes, because the tests' fixture shape happens to
sidestep the realistic case — `#277`'s mocked HTTP boundary, `#280`'s lack of any concurrent-writer
test, and `#282`'s test fixtures using an identical Controller-task-id/Plane-issue-id pairing that
never occurs in production are three separate instances of this SAME meta-pattern in one round alone.
Do not treat "Phase 2 review" as bounded to Phase 2 code, to any fixed set of angles, or to code the
current round didn't itself touch** — every round that tried a genuinely new angle found something
the previous rounds' angles couldn't have found, without exception across all fourteen rounds so far.

## Immediate Next Step: Fix the Audit-Chain CRITICAL, Then the Reconcile HIGH

Prioritize `#280` first (the audit log's own tamper-evidence guarantee silently breaks under
realistic concurrent load — this is the worst finding of any round so far precisely because it's
silent: no exception, no log line, just a forked chain nobody would notice without an explicit
integrity walk). Fix needs an actual mutex for tip advancement (e.g. `pg_advisory_xact_lock` on a
fixed key, or a dedicated single-row locked-by-primary-key tip table), not the current
`ORDER BY/LIMIT + FOR UPDATE` pattern, and a live 15-20-genuinely-concurrent-session regression test
verifying the resulting chain has zero forks — a monkeypatched/mocked version of this test would not
catch a regression, per this round's own lesson. Fix `#281` alongside it (give `compute_hash()` a way
to verify an already-persisted row) so `#280`'s own fix can be self-verified going forward. Then
`#282` (HIGH — thread `Task.plane_issue_id` through `cli.py reconcile`'s `controller_tasks` tuple and
`ReconciliationService.reconcile()`'s matching step, matching the `plane_issue_id or task_id` fallback
`#201` already established for the write path; also fix `tests/test_reconciliation_service.py`'s
fixtures to use non-identical Controller-task-id/Plane-issue-id pairs so this exact bug shape can't
hide behind the tests again). Before declaring Phase 2 gate-clean, run a fresh live-reproduction
review and confirm the live issue list has no open `severity:critical` or `severity:high` issues —
and try an angle no prior round has tried yet, given the track record above. Two concrete leads for
the next round: (1) the `macro_agent_service` sweep in round 14 came back genuinely clean (no
`await` points inside the store's async methods means no interleaving windows exist structurally) —
but nobody has yet applied the SAME "does every documented workflow actually work against a real,
unmocked path" lens used on `#277`/`#282` to the macro-agent-service HTTP boundary itself (e.g. does
the Controller's `MacroAgentClient` genuinely handle every real HTTP failure mode
macro-agent-service can produce, tested against the REAL service process rather than a fake); (2) no
round has yet checked whether OTHER hash-chain-or-similar "was this the tip/latest" patterns exist
elsewhere in the codebase beyond `AuditLog` — a systematic grep for `ORDER BY.*DESC.*LIMIT 1` combined
with `FOR UPDATE` or any "get the latest X" query used as a serialization point would be the RISK-16/
RISK-19-style systematic sweep this specific defect shape deserves.

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
