# Next Steps

## Current State

**Phase 2 hardening is gate-clean as of 2026-08-31. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-31, sixteen review rounds have run. Rounds 1-8
(`#154`-`#258`) landed and hold up on re-verification. Rounds 9-13 established two recurring defect
classes — `RISK-16` (write before CAS, committed regardless of outcome, 3 confirmed instances) and
`RISK-19` (identity-map staleness, 5 confirmed instances, including round 13's `#278` revealing round
6's own `#242` fix had silently never worked) — plus `#277` (HIGH, round 13 — the `approve` CLI
command had never once authenticated against any real deployment, invisible because its own tests
only mocked `httpx.post`). Round 14 confirmed round 13's fixes clean, found opencode's own `#277` fix
had only strengthened a mocked test rather than adding a live-server check (fixed directly), then
found this project's worst finding to date: `#280` (CRITICAL) — the `AuditLog` hash chain, this
project's core tamper-evidence guarantee, silently forked under real concurrent writers — plus `#281`
(MEDIUM, `compute_hash()` unusable on ORM-loaded rows) and `#282` (HIGH, `gc reconcile` never
matched tasks against Plane correctly, an exact recurrence of `#201`'s defect class in code that
fix never touched). **Round 15 confirmed all three of Round 14's fixes (`#280`-`#282`) are genuinely
closed — independently re-reproduced live, including a fresh 20-concurrent-writer chain-integrity
check with zero forks. Per this project's now-standard practice of adversarially reviewing the
IMMEDIATELY PRECEDING round's own fix, this round found `#280`'s fix itself introduces a NEW,
real regression: `#283` (HIGH) — the global `pg_advisory_xact_lock` the fix uses is
TRANSACTION-scoped, and at least three real call sites (`ApprovalService.approve()`,
`VerificationService.verify_and_advance()`, `TaskService.create()`) call `AuditService.log()` and
then make a SLOW outbound Plane HTTP call before committing — meaning the lock stays held across
that entire external call. Live-reproduced: an unrelated, otherwise-instant audit write for a
DIFFERENT task was blocked for the full duration of a simulated 2s Plane call. Since Plane is
explicitly documented as a non-authoritative projection (`CLAUDE.md`), this inverts the architecture
— a slow/degraded Plane instance can now stall EVERY audit-log write system-wide, for any task, via
completely ordinary traffic (any approval, any verification failure). Two genuinely new angles then
found two more real gaps: real, unmocked testing against the live `macro_agent_service` process
found `#285` (HIGH) — an untrusted-boundary response missing an expected field raises an unhandled
`KeyError` OUTSIDE the error-handling `try/except` at two call sites, leaving a task silently stuck
with zero audit trail — and `#286` (MEDIUM) — `Execution.status_error` records only the exception
CLASS NAME, so a genuine 404 (run permanently lost) and a genuine 500 (transient) are indistinguishable,
defeating `#261`'s own original diagnostic purpose. A replay/security audit of every intake/webhook
adapter found `#284` (HIGH) — `TelegramAdapter.process_update`'s `/approve` command has the EXACT
same bug shape as `#277`: it never sends `X-Controller-Secret`, so Telegram-based human approval has
also never worked against any real, properly-configured deployment, in a code path `#277`'s fix
never touched.** **Every round that tried a genuinely new angle has found something no prior round's
angles could have found, without exception across all sixteen rounds so far. A third lesson now
joins the standing two (`RISK-16`/`RISK-19` sweeps; "mocked/unrealistic-fixture tests hide broken
documented workflows"): a fix for one bug can introduce a NEW regression of a DIFFERENT kind — this
round's `#283` is the second time this has happened (the first being `RISK-16`/`RISK-19` themselves
recurring inside their own predecessor fixes) — so every round should keep adversarially reviewing
not just "does the immediately preceding fix close its reported bug" but "does it introduce anything
new," even after confirming the original bug is genuinely gone. `#277`'s lesson has now generalized
twice in one round too: `#284` (Telegram) and `#285`/`#286` (macro-agent HTTP boundary) both surfaced
by finally testing a real, unmocked network boundary nobody had driven live before.** Query the live
issue list before trusting anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of the latest live check: **0 open issues (0 CRITICAL, 0 HIGH, 0 MEDIUM, 0 LOW)**. Issues
`#283`-`#296` are closed and carry the `phase-2` label. Round 16's reviewed commits are pushed
through `74e59a0`; the local gate is green. The two live Plane contract tests remain skipped because
`GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, and `GC_PLANE_PROJECT_ID` are not configured.

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

Test status (2026-08-31): **464 passed / 20 skipped** on SQLite, **482 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (69 source files); `macro_agent_service`
has **10 passed**, with `ruff`/`mypy` clean. The two live Plane contract tests are skipped because
`GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, and `GC_PLANE_PROJECT_ID` are not configured.
**CI is green** (`.github/workflows/ci.yml`, added by `#223`, had failed on all 13 runs since
2026-08-26 until Round 11's CI-infra fix). Round 15 made no
production-code changes of its own beyond what opencode's `8888ddc` fix commit already covered
(that commit's own new tests account for this round's count increase over Round 14's 429/435). Green
tests are still not evidence of correctness in this project beyond "nothing crashes" — none of round
8's through round 15's findings (`#253`-`#286`), including all six CRITICALs found across those
rounds, were caught by this suite before their respective fixes landed; they required killing a live
process, adversarially re-reviewing the immediately preceding round's own fix, throwing genuinely
concurrent real HTTP/DB load at a live server or real Postgres, or — for `#277`/`#280`/`#282`/`#284`
/`#285` — simply trying to actually exercise a documented workflow or a real (not mocked) network
boundary that every existing test's fixture shape happened to sidestep.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Fifteen review
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
audit log's own hash chain, `#280`-`#282`), Round 15 (verification of round 14's fixes, an
adversarial review of `#280`'s OWN fix for a new regression, real HTTP-boundary testing against the
live `macro_agent_service` process, a systematic sweep for other tip-lookup serialization patterns,
and a replay/security audit of every intake/webhook adapter, `#283`-`#286`). Round 16 completed the
follow-up hardening for retry-start recovery (`#289`), approval/retry external-run CAS handling
(`#293`, `#294`), Plane traceability and property projection (`#290`), durable CLI Plane retries
(`#288`, `#295`, `#296`), reconciliation DAG IDs (`#291`), and malformed pagination (`#292`). The
changes are pushed through `74e59a0`; issues `#283`-`#296` are closed and relabeled `phase-2`. The
current local gate is green, but live Plane contract tests remain skipped because credentials are not
configured. Keep the adversarial review practice: green tests do not replace live boundary checks,
and no phase may be declared complete while critical/high issue queries remain non-empty.

## Immediate Next Step: Phase 3 Preparation

Phase 2 hardening is gate-clean. Before starting Phase 3, keep the live critical/high issue queries
empty and decide whether to provision a Plane instance for the skipped live contract tests.

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
