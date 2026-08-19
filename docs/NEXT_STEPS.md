# Next Steps

## Current State

**REVIEW-023 (a full fresh review, prompted specifically to look past the zero-open-rows milestone REVIEW-022 reached) reopened the gate: 3 new `HIGH` gaps found.**

- `GAP-095`: `EventBridge.handle()`'s `AGENT_REVIEW` transition is never committed before running verification commands, so the row lock is held for as long as an untimed, proposer-controlled shell command takes — the same defect class `GAP-080` fixed in `_trigger_execution`, in a sibling path that fix never touched, and worse here because it's fully unbounded (no timeout at all). Live-reproduced against real Postgres.
- `GAP-096`: `PermissionService.may_approve()`'s non-human-actor blocklist is an exact-literal check against `{"system", "agent"}`, but SPEC-03 itself documents structured actor identifiers like `system:macro-agent` — any such identity sails through and can approve a PLAN it didn't propose. Live-reproduced through the real API. Directly implicates AGENTS.md's non-negotiable self-approval rule.
- `GAP-097`: a transient failure of the outbound macro-agent call during a verification retry skips recording the event's dedup key, so the mandatory SPEC-05 §5.4 redelivery reprocesses the same event as new and double-consumes the retry budget. Live-reproduced.

Also new this round, non-blocking: `GAP-098` (MEDIUM, `GAP-094`'s SQLite fix over-broadly also silences pool settings for file-based SQLite, not just `:memory:` — the identical `RISK-18` pattern one level deeper), `GAP-099`/`GAP-100`/`GAP-101` (MEDIUM — a `BLOCKED` task can be un-blocked by any `RUNNING`-mapped event, not just `conflict:resolved`; a comma in a policy-violation message corrupts the API's structured `violations` array; `TaskContract.execution.role` is policy-validated but never forwarded to macro-agent), `GAP-102` (MEDIUM, closed directly — `SPEC-03` §3.3 was missing `404`/`503` from its documented response codes), `GAP-103`/`GAP-104`/`GAP-105`/`GAP-106` (LOW — dead code with a false docstring, a test-isolation leak, the SDD progress ledger stale again, `TaskResponse` not exposing `execution_attempts`).

Every gap closed through REVIEW-022 remains closed, independently re-verified by live reproduction across the series (not just diff review) — see `reviews/GAPS.md` for the full ledger. This round is a reminder that "zero open rows" describes the state of *known* defects at that moment, not an assurance nothing remains to find — REVIEW-023 found 3 new `HIGH` issues in code no prior round's fresh-eyes pass had specifically targeted (the `EventBridge`↔verification lock interaction, the permission blocklist's literal-string matching, and the retry-mechanism's own exception path).

Test status: **212 passed**, `ruff` clean, `mypy --strict` clean — none of this round's findings are tooling-detectable.

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
  (the actor blocklist's exact-string matching has a known gap against structured `system:*`
  identifiers — see `GAP-096`).
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
   - Build a periodic job that compares live Plane state and Controller DB state.
   - Detect divergence, raise human alert, and auto-correct only projection fields.
   - *Deferred to Phase 2 because the Plane integration is still a stub in Phase 1.*

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

8. **Meta Orchestrator integration** (SPEC-10 §10.2 assigns this to Phase 2, not Phase 3+)
   - OpenCode + BMAD + OpenSpec integration.
   - Evaluate sudocode-ai/sudocode's Spec/Issue graph model and OpenSpec integration before building
     idea-decomposition tooling from scratch (its execution engine is out of scope; that's
     macro-agent's job) — see `docs/research-alexngai-ecosystem-and-sudocode.md`.

9. **Intake Adapter** (SPEC-10 §10.2 assigns this to Phase 2, not Phase 3+)
   - Telegram/Email/API intake, feeding the Idea Ingestion Service.
   - Human Triage queue in Plane.

10. **Verification failure feedback to macro-agent (SPEC-09 §9.6 step 2)**
    - `GAP-077`'s retry loop genuinely retries and restarts execution (`GAP-085`, `GAP-077` both
      `CLOSED`, independently re-verified). The remaining gap: no failure-feedback message is emitted
      to macro-agent so it can repair and re-land; `verification_service.py` still has a `# TODO`
      marking this. `GAP-097` (HIGH, OPEN) also needs fixing in this area: a transient failure of the
      outbound macro-agent call during a retry skips recording the event's dedup key, so a redelivery
      of that event (which SPEC-05 §5.4 requires on failure) double-consumes the retry budget.

11. **Real outbound alert channel for terminal FAILED (SPEC-09 §9.6 step 3)**
    - `GAP-090` added a real, tested `alert_human` audit-log marker — a human can now find terminal
      failures by querying the audit log, but there's still no webhook/email/Plane-comment push; a
      human must still poll to discover the failure.

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
