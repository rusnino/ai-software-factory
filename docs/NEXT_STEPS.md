# Next Steps

## Current State

**Phase 1 is not done.** `reviews/REVIEW-005-round3-gap-verification.md` found that 2 of the 6 claimed
`HIGH` fixes above don't actually work, reopening `GAP-023` and `GAP-024` (plus downgrading `GAP-031`/
`GAP-032` and opening a new test-coverage gap, `GAP-043`):

- `GAP-022` (CRITICAL) genuinely fixed — `get_db()` commit/rollback proven correct by direct reproduction
  against real session semantics (not just the test suite, which still can't exercise this path — `GAP-043`).
- `GAP-023` (HIGH) **not actually fixed** — the "optimistic version guard" is dead code: `AsyncSessionLocal`'s
  `expire_on_commit=False` plus a missing `populate_existing=True`/`session.refresh()` means the re-fetch
  under lock returns the *same cached object* already held by the caller, so the version-mismatch branch can
  never fire. `.with_for_update()` is also a silent no-op on SQLite. Concurrent approvals can still
  double-trigger `MacroAgentExecutor.start()`.
- `GAP-024` (HIGH) **partially fixed** — `PolicyEngine` now genuinely validates `CompletionContract.command`
  content (catches a contract that lies about `destructive_shell`). But `TaskContract.verification.commands`
  — the exact field this gap named — is wired into `VerificationService` with **zero** `PolicyEngine`
  validation, a live, untested bypass to the same class of unmitigated shell execution.
- `GAP-025`, `GAP-026`, `GAP-020`, `GAP-028`, `GAP-029`, `GAP-030`, `GAP-033`, `GAP-034`, `GAP-038`, `GAP-040`
  are genuinely closed, confirmed by live reproduction/test runs, not just commit messages.

Test status: **142 passed**, `ruff` clean, `mypy --strict` clean — all independently confirmed accurate, but
**not sufficient evidence of correctness for GAP-023/024** specifically (see REVIEW-005 for why: the tests
covering both pass for reasons unrelated to the mechanisms they claim to validate).

Implemented components:

- FastAPI application with `POST /tasks`, `POST /approvals`, `GET /health`, `GET /tasks/{id}`,
  `GET /executions/{id}`, and `GET /tasks/{task_id}/audit-log`.
- SQLModel async PostgreSQL models: `Task`, `Execution`, `Approval`, `AuditLog`, `ProcessedEvent`.
- Deterministic state machine covering `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
- Embedded Policy Engine validating task contracts, project profiles, harness allowlist,
  forbidden paths (with path-prefix matching), security posture, git settings, approval chain, and
  `CompletionContract` shell-command allowlisting.
- Append-only `AuditService` wired into task/profile creation, approvals, state transitions,
  execution starts, macro-agent events, and verification results.
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors.
- `ApprovalService` as the single convergence point for all approvals, with `Idempotency-Key`
  header support and key-based deduplication.
- Plane Adapter interface + in-memory stub.
- Harness Provider Registry with OpenCode and Claude Code metadata, including per-harness `allowed_roles`
  aligned with SPEC-06 §6.2.
- macro-agent executor abstraction (`MacroAgentClient`, `MacroAgentExecutor`) and `Execution` model,
  with explicit configurable HTTP timeout and outbound traceability metadata per SPEC-05 §5.7.
- Automatic execution trigger after `EXECUTION` approval, with `READY → RUNNING` transition.
- opentasks materialization stub with DAG validation.
- In-process macro-agent Event Bridge translating workspace events into Controller state updates;
  `landing:completed` triggers `VerificationService` and gates `AGENT_REVIEW → HUMAN_REVIEW`, with
  event-level idempotency.
- Dockerfile and Docker Compose for local API + PostgreSQL; image runs as non-root `controller` user.
- CLI (`approve`) and Telegram adapter stubs converging on `POST /approvals`.
- Verification service executes `CompletionContract`/`TaskContract.verification` `required`/`optional`
  shell commands, compares exit codes, and performs forbidden-path/scope checks.
- End-to-end Phase 1 smoke test.

## Phase 1 Stop Conditions

None declared. OpenCode integration remains a stub path; no ACP/MCP blocker was encountered.

## Immediate Next Step: Phase 2

Priority: integrate with real external systems and harden execution orchestration.

### Candidate Tasks

1. **Real Plane CE sync**
   - Implement HTTP Plane Adapter using Plane REST API.
   - Bidirectional task state sync (read state/comments, write state/comments).
   - Webhook receiver for Plane → Controller events.

2. **Real macro-agent integration**
   - Replace `MacroAgentClient` HTTP stub with actual macro-agent `/runs` API.
   - Poll/collect execution results.
   - Handle conflict recovery events.

3. **Reconciliation (SPEC-03 §3.9)**
   - Build a periodic job that compares live Plane state, Controller DB state, and opentasks runtime graph.
   - Detect divergence, raise human alert, and auto-correct only projection fields.
   - *Deferred to Phase 2 because both Plane and opentasks integrations are still stubs in Phase 1.*

4. **Durable execution**
   - Evaluate Temporal or Celery for retry/collect workflows.
   - Persist runtime task graph from opentasks materializer.

5. **Policy Engine backend**
   - Integrate Open Policy Agent (OPA) as optional policy backend.

6. **Advanced isolation**
   - Docker-based agent sandbox.
   - Evaluate Firecracker / Kata for Phase 3.

7. **Security hardening**
   - Authentication/authorization middleware.
   - Secret injection via environment or vault, never in prompts/YAML.

## Blockers to Watch

- macro-agent API stability and `/runs` contract.
- OpenCode ACP compatibility with macro-agent MCP tools.
- Plane CE self-hosted availability and API rate limits.

## Deferred to Phase 3+

- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Meta Orchestrator integration.
- Intake Adapter (non-Plane task ingestion).
- Semantic Reviewer.
- Production hardening (metrics, tracing, HA).
- Evaluate LongHorizon-Harness (or similar durable-execution wrappers) as an optional `AgentHarness`
  adapter for long-running/GUI-touching opentasks — see `docs/research-longhorizon-harness.md`.
