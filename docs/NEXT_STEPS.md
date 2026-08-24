# Next Steps

## Current State

**Phase 1 is complete** (all `phase-1`-labeled `severity:critical`/`severity:high` issues closed).
**Phase 2 is NOT gate-clean.** The fix round that landed after the first review closed 12 of 13
issues genuinely, but independent re-verification found `#152`'s fix (the "parity" OPA Rego policy)
does not actually work — the shipped `governance.rego` fails to parse in a real OPA server (`opa
check` reports a syntax error), is missing the `#141` `sed` RCE check, and is architecturally a
denylist rather than a mirror of the embedded engine's allowlist. `#152` has been reopened with the
full evidence. A separate small `#164` bundles three minor residuals (no `--fix` flag on the
`reconcile` CLI, zero test coverage on the new macro-agent feedback path, a missing RISK
cross-reference) that don't reopen their original issues but are worth closing. The live issue list
is the authoritative source of truth; run the queries below before declaring anything done.

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
but whose shipped "parity" Rego policy does not actually work (`#152`, reopened — the file fails to
parse in real OPA, is missing the `#141` sed check, and isn't an allowlist). Most Phase 2 review
findings were closed with code or documented decisions; `#152` and the small `#164` follow-ups
were not.

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
verification feedback + alerting, and optional OPA backend. The 2026-08-24 review round opened 13
issues; 12 were genuinely closed on independent re-verification. `#152` was reopened — its "parity"
OPA Rego policy doesn't parse in a real OPA server, is missing the `#141` sed check, and isn't an
allowlist. **Phase 2 is NOT gate-clean.** Do not enable `GC_OPA_BASE_URL` against the shipped policy
outside a test until `#152` closes for real.

## Immediate Next Step: Close #152 and #164, Then Phase 3

Close the reopened `#152` (OPA parity policy) and the small `#164` follow-ups before treating Phase
2 as done. Once the live issue list genuinely has no open `phase-2` issues, Phase 3 scope (from
SPEC-10 §10.3) is:

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
