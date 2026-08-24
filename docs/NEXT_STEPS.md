# Next Steps

## Current State

**Phase 1 is complete** (all `phase-1`-labeled `severity:critical`/`severity:high` issues closed).
**Phase 2's 10 SDD tasks (SPEC-10 §10.2: Plane sync, macro-agent service, opentasks materializer,
reconciliation, intake adapter, OPA backend) have all landed, but Phase 2 is NOT gate-clean** — its
first review round (2026-08-24) found 13 open issues, including one `severity:critical` (an
unauthenticated Plane webhook that lets anyone forge approvals) and four `severity:high`. Do not
treat Phase 2 as done until that gate clears the same way Phase 1's did.

Gap tracking lives in GitHub Issues (see `AGENTS.md` §"Review Findings and Gap Tracking"). Use the
live issue list for current state — this is the authoritative source, not any prose summary below:

```bash
gh issue list --repo rusnino/ai-software-factory --state open --label phase-1
gh issue list --repo rusnino/ai-software-factory --state open --label phase-2
gh issue list --repo rusnino/ai-software-factory --state open --label severity:critical
gh issue list --repo rusnino/ai-software-factory --state open --label severity:high
```

Phase 1 architectural summary: command validation uses an explicit `argv[0]` allowlist plus
per-binary dangerous-construct checks. Known-resolved bypass classes include wrapper/interpreter
smuggling, forbidden-path text scanning, destructive flags on allowlisted binaries,
command-execution primitives such as `git -c`, `tar --to-command`, `find -exec`, and `sed` `s///e`,
container escape flags, and removal of unauditable network/package-manager tools (`curl`, `wget`,
`apt`, `apt-get`, `dpkg`).

Phase 2 architectural summary: real Plane CE HTTP client + webhook receiver + Controller→Plane
projection; a macro-agent-facing HTTP client wired to a **self-declared Python stand-in** service
(not the real `macro-agent` package — `#151`); an opentasks DAG materializer reading live Plane
dependencies, wired into `EXEC_APPROVED -> READY`; a reconciliation service/CLI that — per the
current review round — does not actually enforce or even read real data yet (`#156`); an intake
adapter (Telegram/Email/generic) creating Plane drafts; an optional OPA policy backend that, when
enabled, bypasses 100% of Phase 1's command-validation hardening with no equivalent policy shipped
(`#152`). See `gh issue list --label phase-2` for the full, current picture.

Test status: **338 passed / 5 skipped** on SQLite, **341 passed / 2 skipped** on PostgreSQL,
`ruff` clean, `mypy governance_controller` clean (66 source files). Passing tests do not imply
Phase 2 is safe to rely on — see the CRITICAL/HIGH issues above; the suite doesn't yet cover them.

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

## Immediate Next Step: Close the Phase 2 Review Gate

Priority: **close the 13 open `phase-2` issues from the 2026-08-24 review round before treating any
Phase 2 task as done** — especially the CRITICAL webhook-auth bypass (`#154`) and the four HIGH
issues (`#152` OPA bypass, `#155` materializer recursion crash, `#156` inert reconciliation, `#157`
intake DoS). Query `gh issue list --repo rusnino/ai-software-factory --state open --label phase-2`
for the current list rather than trusting a static summary here — this file will not be kept
manually in sync issue-by-issue (see `#161`, `#145`, `#136` for why that approach kept failing).

All 10 of Phase 2's originally-scoped SDD tasks (Plane sync, macro-agent client, reconciliation,
durable-execution DAG materialization, OPA backend, security-relevant intake/webhook surface, Meta
Orchestrator groundwork via the intake→triage path, verification feedback, outbound alerting) have
landed in some form — "landed" here means code exists and its own unit tests pass, **not** that the
review round found it correct or safe. Treat the issue tracker, not this list, as the source of
truth for what still needs work on any of them.

### Genuinely not started yet (not touched by the Phase 2 round above)

- **Durable execution**: evaluate Temporal or Celery for retry/collect workflows; persist the
  runtime task graph from the opentasks materializer beyond in-memory construction.
- **Advanced isolation / Docker sandboxing**: `SPEC-10 §10.3` assigns this to Phase 3. Scoped at
  ~1.5-3 weeks solo-engineer effort, with the execution-backend architecture (Docker socket in
  Controller vs. a dedicated sandbox-executor sidecar vs. delegating to macro-agent's own sandbox)
  as the biggest open decision — see `docs/research-verification-sandboxing-scope-2026-08-24.md`.
  Not a substitute for closing `#154`/`#157` — sandboxing bounds damage from a wrongly-approved
  command; it doesn't fix approvals or intake endpoints having no auth in the first place.
- **Meta Orchestrator (OpenCode + BMAD + OpenSpec)**: the intake→idea-ingestion path exists, but
  the actual decomposition/planning engine doesn't. Evaluate sudocode-ai/sudocode's Spec/Issue graph
  model before building this from scratch — see `docs/research-alexngai-ecosystem-and-sudocode.md`.
- **Real `macro-agent@latest` integration**: still a self-declared Python stand-in, not the actual
  npm package — see `#151` for the full analysis of why, and the decision this needs.

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
