# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, ten review rounds have run. Rounds 1-7
(`#154`-`#249`) landed and hold up on re-verification. Round 8 found two issues that were, at the
time, the most severe of any round: a Controller crash at either of two specific points in the
approval/verification pipeline permanently strands a task with NO automatic recovery path
(`#253`/`#254`). Round 9 verified opencode's `#253`/`#254` fix and found the fix code itself reused
this project's single most recurring defect class — "blind write committed before a CAS check,
regardless of the CAS outcome" (`REQUIREMENTS.md` `RISK-16`) — introducing two MORE CRITICALs
(`#262`, `#263`), plus reopened `#255` (HIGH) and five MEDIUM gaps (`#259`-`#261`, `#264`-`#265`).
**Round 10 verified ALL SEVEN of Round 9's fixes are genuinely closed via live races and a real
Docker build — but found the exact same `RISK-16` pattern a THIRD time, in code no prior round had
looked at: `#266` (CRITICAL — `VerificationService._start_retry_execution` flushes
`Execution.state = FAILED` before its own `Task`-level CAS to `FAILED`, and commits regardless of
whether that CAS wins, live-reproduced with two real concurrent Postgres sessions leaving
`Task.state == BLOCKED` while `Execution.state == FAILED`). Round 10 also found `#267` (HIGH — the
ONLY documented path to unblock a `BLOCKED` task, the `conflict:resolved` event, uses the exact same
flat, unscoped shared secret as every routine automated macro-agent event, with no human/actor/role/
project check at all — live-reproduced unblocking an arbitrary task via a bare `curl` call), `#268`
(HIGH — `run_migrations()` never `ALTER`s the existing `execution` table for the new `status_error`
column `#261`'s fix added, so every execution-trigger INSERT breaks with `UndefinedColumnError` on
any already-deployed Postgres instance until manually migrated), plus `#269` (MEDIUM — the one CAS
call site in `EventBridge.handle()` writes zero audit entries on a lost race, unlike every sibling
CAS-failure branch elsewhere) and `#270` (LOW — dead, currently-harmless duplicated code left inside
`_mark_blocked` by Round 9's own fix commit).** **Every round that tried a genuinely new angle —
Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7, process-crash resilience
in round 8, adversarial review of round 8's OWN fix code in round 9, a systematic sweep of every
OTHER CAS call site in round 10 — found something no prior round's angles could have found. Two
consecutive rounds now have found a fresh instance of the exact same `RISK-16` defect class in code
the immediately preceding round's own fix touched or introduced — this is now this project's most
reliable predictor of where the next bug will be.** Query the live issue list before trusting
anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **5 open issues (1 CRITICAL, 2 HIGH, 1 MEDIUM, 1 LOW)** — `#266` (CRITICAL — the
third live instance of the `RISK-16` blind-write-before-CAS class, this time in
`_start_retry_execution`'s failure handler), `#267` (HIGH — `conflict:resolved` unblock path has no
human-scoped auth), `#268` (HIGH — missing `execution.status_error` migration breaks execution
INSERTs on upgrade), `#269` (MEDIUM — `EventBridge.handle()`'s CAS-failure branch has zero audit
trail), `#270` (LOW — dead trailing code in `_mark_blocked`). Every prior round's findings
(`#151`-`#265`) are closed and independently re-verified — this is the first round since round 6
where every finding from the immediately preceding round was confirmed genuinely fixed with none
reopened.

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
tests still pass, `ruff`/`mypy` clean. Round 10 made no production-code changes (pure review round),
so these numbers are unchanged from Round 9. Green tests are not evidence of correctness in this
project — re-read the "Current State" section above before trusting this number to mean anything
beyond "nothing crashes." None of round 8's, 9's, or 10's findings (`#253`-`#270`) — including all
five CRITICALs found across the three rounds — were caught by this suite; round 8's were found by
killing a live process and inspecting real Postgres state afterward, round 9's and round 10's by
adversarial code review of the immediately preceding round's own fix followed by live reproduction
of the resulting race with real concurrent Postgres sessions. A real CI workflow exists
(`.github/workflows/ci.yml`, added by `#223`), but it runs this same suite, so it would not have
caught any of these either.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Ten review
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
concurrent-approval racing against a live server, `#266`-`#270`). **5 issues remain open, including
1 CRITICAL.** Round 10 verified all seven of round 9's fixes are genuinely closed — the first time
since round 6 that an entire round's findings held up with none reopened — but the systematic
`atomic_transition` sweep found a THIRD live instance of the exact `RISK-16` "blind write committed
regardless of CAS outcome" pattern, this time in `VerificationService._start_retry_execution`
(`#266`), in code no prior round had examined. **Two consecutive rounds have now each found a fresh
instance of this same defect class in code the immediately preceding round's own fix touched or
introduced (`#262`/`#263` in round 9's review of round 8's fix; `#266` in round 10's systematic
sweep, itself prompted by round 9's own findings) — this pattern is now this project's most reliable
predictor of where the next bug will be, and the systematic sweep approach round 10 used (checking
every OTHER call site of a helper once a bug is found at one call site) is now the recommended
default for any future `RISK-16`-shaped finding, not just spot-checking the fix.** Round 10 also
found a genuinely new angle no prior round had tried — event-level authorization — and it paid off
immediately: `#267` (HIGH), the only documented path to unblock a `BLOCKED` task uses the same flat
credential as routine automated traffic, with no human-scoping at all. **Do not treat "Phase 2
review" as bounded to Phase 2 code, to any fixed set of angles, or to code the current round didn't
itself touch** — every round that tried a genuinely new angle found something the previous rounds'
angles couldn't have found. The next round should keep trying new angles.

## Immediate Next Step: Fix the New CRITICAL, Then the Two New HIGHs, Then Verify Gate-Clean

Prioritize `#266` first (the third live instance of the `RISK-16` defect class — `_start_retry_execution`
flushes `Execution.state = FAILED` before its own CAS, commits regardless of the CAS outcome; fix
needs independent adversarial/live-race verification before being trusted, not just a green test
suite — this is now the established pattern for this defect class). Then the 2 HIGH issues (`#267`
conflict:resolved has no human-scoped auth; `#268` missing execution.status_error migration breaks
execution INSERTs on upgrade) and the MEDIUM/LOW (`#269` EventBridge CAS-failure branch has zero
audit trail; `#270` dead trailing code in `_mark_blocked`). Before declaring Phase 2 gate-clean, run
a fresh live-reproduction review and confirm the live issue list has no open `severity:critical` or
`severity:high` issues — and try an angle no prior round has tried yet, given the track record
above. Specifically worth trying next: `#267`'s finding (event-level authorization) suggests a
broader angle — a dedicated review of every OTHER event type's authorization/actor-trust boundary in
`EventBridge`, not just `conflict:resolved`, has not yet been done systematically.

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
