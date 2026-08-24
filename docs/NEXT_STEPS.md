# Next Steps

## Current State

**Phase 1 is complete** (all `phase-1`-labeled `severity:critical`/`severity:high` issues closed).
**Phase 2 is NOT gate-clean — 23 open `phase-2` issues as of 2026-08-24, including 3
`severity:critical` and 5 `severity:high`.** A first review round (13 issues) mostly got fixed for
real, except `#152` (OPA Rego policy doesn't parse — reopened). A follow-up **task-by-task review of
all 10 Phase 2 SDD tasks** then found substantially more, including three new CRITICAL findings, all
live-reproduced against the real running Plane CE and real macro-agent service stub:
- `#165`: the webhook's shared-secret fix (`#154`) authenticates the *request*, not the *content* —
  `actor` is still a self-declared string, so self-approval is trivially bypassed by respelling it.
- `#166`: reconciliation compares a raw Plane state UUID against a human-readable name — every
  correctly-synced task is flagged as a false-positive divergence.
- `#167`: the Controller's own `MacroAgentClient` never sends the auth secret `#162` added — turning
  that secret on breaks every real execution start.

Do not treat any Phase 2 component as safe to rely on until the live issue list is clean. This file
will not attempt to itemize the remaining 20 by number here (see `#136`/`#145`/`#161` for why that
approach keeps going stale) — query live:

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

Phase 2 architectural summary: real Plane CE HTTP client + authenticated webhook receiver +
Controller→Plane projection service; a macro-agent-facing HTTP client wired to a self-declared
Python stand-in service (real `macro-agent` package deferred to Phase 3 — see
`decisions/ADR-002-macro-agent-stub-vs-package.md`); an opentasks DAG materializer reading live
Plane dependencies with guarded cycle detection; a reconciliation service/CLI that reads Controller
DB state and can fix Plane state divergences; authenticated intake adapters (Telegram/Email/generic)
creating HTML-escaped Plane drafts; verification failure feedback to macro-agent and terminal
alerting; optional OPA policy backend whose network-level fail-closed handling is fixed (`#158`)
but whose shipped "parity" Rego policy does not actually work (`#152`, reopened). A task-by-task
review (2026-08-24) then found the webhook's self-approval guard is separately forgeable (`#165`,
independent of `#152`/`#154`), reconciliation's state comparison is broken against real Plane
(`#166`), and the Controller can't actually talk to macro-agent once `#162`'s auth is turned on
(`#167`) — plus 20 more HIGH/MEDIUM/LOW findings (`#168`-`#185`). Every component exists and its own
unit tests pass; that is not evidence any of it is correct against real Plane/macro-agent behavior,
which the unit tests' mocks don't reproduce. Query the live issue list, not this paragraph.

Test status: **351 passed / 5 skipped** on SQLite, **354 passed / 2 skipped** on PostgreSQL,
`ruff` clean, `mypy governance_controller` clean (66 source files); `macro_agent_service` tests
**7 passed**, `ruff`/`mypy` clean.

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

All 10 Phase 2 SDD tasks landed in `main` between commits `7c0bd5c` and `4715c22`: real Plane sync,
macro-agent client/service scaffold, opentasks materializer, reconciliation, intake adapter,
verification feedback + alerting, and optional OPA backend. Two review rounds followed: the first
(13 issues) got 12 genuinely fixed, `#152` reopened. A second, task-by-task review of all 10 SDD
tasks against the live Plane CE / macro-agent stub found 21 more issues (3 CRITICAL, 4 HIGH, 10
MEDIUM, 4 LOW) — every one of them live-reproduced, not inferred from reading code. **Phase 2 is NOT
gate-clean and should not be treated as such until the live issue list — 23 open `phase-2` issues as
of this writing — is empty.** In particular: do not enable `GC_OPA_BASE_URL` (`#152`), do not rely on
the Plane webhook's self-approval guard (`#165`), do not trust `reconcile`'s output (`#166`), and do
not set `MACRO_AGENT_SERVICE_API_SECRET` without also fixing the Controller's client (`#167`).

## Immediate Next Step: Close the 23 Open phase-2 Issues, Then Phase 3

Prioritize the 3 CRITICAL (`#165` webhook self-approval bypass, `#166` reconciliation false
positives, `#167` macro-agent client auth breakage) and 5 HIGH (`#152`, `#168`-`#171`) issues first.
Once the live issue list genuinely has no open `phase-2` issues, Phase 3 scope (from SPEC-10 §10.3)
is:

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
