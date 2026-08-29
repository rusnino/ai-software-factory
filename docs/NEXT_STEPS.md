# Next Steps

## Current State

**Neither phase is gate-clean. Do not trust a "gate-clean" claim in this file's own history —
it has been declared prematurely at least three separate times, each later found wrong by
independent live verification.** As of 2026-08-30, eight review rounds have run. Rounds 1-6
(`#154`-`#242`) landed and hold up on re-verification. Round 7 found the project's then-most-severe
issue — cross-project `ProjectProfile` poisoning via `POST /tasks` (`#244`) — by trying a genuinely
new angle (multi-tenancy isolation) no prior round had tried. **Round 8 found two issues more severe
than that: a Controller crash at either of two specific points in the approval/verification pipeline
permanently strands a task with NO automatic recovery path, and for one of them, no manual recovery
path either short of hand-editing Postgres** (`#253`: crash between the `READY` commit and the
macro-agent call; `#254`: crash during verification, where the very dedup-marker mechanism `#240`
added to prevent duplicate verification now actively suppresses the event redelivery that would
otherwise recover the task). Round 8 also found a HIGH path-traversal in `TaskContract.task_id`
(`#255`, redirects a task's own completion-contract checks' cwd/`$HOME` to any directory on the
Controller host) and that round 7's own rate-limit fix (`#248`) reintroduced the exact unbounded-
growth bug it was fixed alongside (`#250`, in `#243`). **Every round that tried a genuinely new
angle — Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7, process-crash
resilience in round 8 — found something no prior round's angles could have found.** Query the live
issue list before trusting anything else in this file:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
```

As of this writing: **9 open issues (2 CRITICAL, 3 HIGH, 2 MEDIUM, 2 LOW)** — `#253`/`#254`
(CRITICAL — unrecoverable stuck states from a Controller crash mid-pipeline), `#255` (HIGH —
`task_id` path traversal into the verification worktree cwd), `#250` (HIGH — the round-7 rate-limit
fix leaks memory unboundedly, same bug class as `#243`), `#256` (HIGH — intake duplicate/rate-limit
guard is dead code whenever Plane isn't configured), `#252` (MEDIUM — three Plane write call sites
drop `project_id`, falling back to the wrong project), `#257` (MEDIUM — Telegram actor derivation
trusts a mutable username over the stable numeric id), plus two LOW items (`#251`, `#258`). Every
prior round's findings (`#151`-`#249`) are closed and independently re-verified.

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

Test status (2026-08-30): **413 passed / 5 skipped** on SQLite, **416 passed / 2 skipped** on
PostgreSQL, `ruff` clean, `mypy governance_controller` clean (68 source files); `macro_agent_service`
tests still pass, `ruff`/`mypy` clean. Green tests are not evidence of correctness in this project —
re-read the "Current State" section above before trusting this number to mean anything beyond
"nothing crashes." None of round 8's findings (`#253`-`#258`) — including two CRITICAL unrecoverable
stuck-state bugs — were caught by this suite; they required actually killing a live process and
inspecting real Postgres state afterward. A real CI workflow exists (`.github/workflows/ci.yml`,
added by `#223`), but it runs this same suite, so it would not have caught these either.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`. Eight review
rounds have run since: Round 1 (`#154`-`#163`), Round 2 (`#164`-`#185`), Round 3 (`#186`-`#213`),
Round 4 (fix-batch verification + fresh audit, `#189`-`#221` reopened/new), Round 5 (Phase 1 core,
deployment/CI, schema validation, docs-accuracy sweep, `#222`-`#234`), Round 6 (adversarial review
of rounds 4-6's own new code, GET-endpoint auth audit, concurrency sweep, fresh end-to-end pipeline,
`#236`-`#242`), Round 7 (macro-agent adversarial testing, cross-project isolation, resource
limits/rate limiting, secret-leakage audit, `#243`-`#249`), Round 8 (adversarial review of round 7's
own fixes, systematic project-scoping sweep, Controller crash/restart resilience, unconstrained
schema fields, `#250`-`#258`). **9 issues remain open, including 2 CRITICAL.** Round 8's two
CRITICALs (`#253`, `#254`) are the most severe findings of any round so far — a Controller process
crash at either of two specific points in the approval/verification pipeline leaves a task
permanently stuck with no automatic recovery, and for `#254` specifically, the crash also disables
the one mechanism (event redelivery) that would otherwise have recovered it. Nobody had tested
process-level crash resilience before round 8 — every round so far tested request-level races and
logic, never "what if the Controller itself dies mid-operation." **Do not treat "Phase 2 review" as
bounded to Phase 2 code, or to any fixed set of angles** — every round that tried a genuinely new
angle (Phase 1 core in round 5, the read side in round 6, cross-tenancy in round 7, crash resilience
in round 8) found something the previous rounds' angles couldn't have found. The next round should
keep trying new angles, not repeat the ones already covered.

## Immediate Next Step: Fix the Two Unrecoverable-Crash CRITICALs, Then Verify Gate-Clean

Prioritize `#253` and `#254` first (a Controller crash mid-pipeline permanently strands a task with
no recovery path — this is a live, reproducible reliability gap in the current codebase, not a
theoretical one, and both were confirmed by actually killing a real process and inspecting real
Postgres state afterward). Then the 3 HIGH issues (`#255` task_id path traversal, `#250` rate-limit
middleware memory leak, `#256` intake guard dead-code-without-Plane) and 2 MEDIUM (`#252` Plane
writes dropping project_id, `#257` Telegram username-vs-id trust). Before declaring Phase 2
gate-clean, run a fresh live-reproduction review and confirm the live issue list has no open
`severity:critical` or `severity:high` issues — and try an angle no prior round has tried yet, given
the track record above.

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
