# Next Steps

## Current State

Phase 1 Governance Controller is **complete, fully tested, and review gaps are closed** in
`src/governance_controller/`. All `CRITICAL`, `HIGH`, `MEDIUM`, and `LOW` findings from
`reviews/REVIEW-001-phase-1-governance-controller.md` are now `CLOSED` in `reviews/GAPS.md`.

Final test status: **130 passed**, ruff clean.

Implemented components:

- FastAPI application with `POST /tasks`, `POST /approvals`, `GET /health`, `GET /tasks/{id}`,
  `GET /executions/{id}`, and `GET /tasks/{task_id}/audit-log`.
- SQLModel async PostgreSQL models: `Task`, `Execution`, `Approval`, `AuditLog`.
- Deterministic state machine covering `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
- Embedded Policy Engine validating task contracts, project profiles, harness allowlist,
  forbidden paths, security posture, git settings, and approval chain.
- Append-only `AuditService` wired into task/profile creation, approvals, state transitions,
  execution starts, macro-agent events, and reconciliation checks.
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors.
- `ApprovalService` as the single convergence point for all approvals, with `Idempotency-Key`
  header support and key-based deduplication.
- Plane Adapter interface + in-memory stub.
- Harness Provider Registry with OpenCode and Claude Code metadata.
- macro-agent executor abstraction (`MacroAgentClient`, `MacroAgentExecutor`) and `Execution` model.
- Automatic execution trigger after `EXECUTION` approval, with `READY → RUNNING` transition.
- opentasks materialization stub with DAG validation.
- In-process macro-agent Event Bridge translating workspace events into Controller state updates.
- Minimal reconciliation service stub for Plane/Controller divergence detection.
- Dockerfile and Docker Compose for local API + PostgreSQL.
- CLI (`approve`) and Telegram adapter stubs converging on `POST /approvals`.
- Verification service driven by `CompletionContract` when present, with fallback checks.
- Git-cascade landing stub with branch validation.
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

3. **Durable execution**
   - Evaluate Temporal or Celery for retry/collect workflows.
   - Persist runtime task graph from opentasks materializer.

4. **Policy Engine backend**
   - Integrate Open Policy Agent (OPA) as optional policy backend.

5. **Advanced isolation**
   - Docker-based agent sandbox.
   - Evaluate Firecracker / Kata for Phase 3.

6. **Security hardening**
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
