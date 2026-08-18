# Next Steps

## Current State

Phase 1 Governance Controller is **complete and fully tested** in `src/governance_controller/`.

Implemented components:

- FastAPI application with `POST /tasks`, `POST /approvals`, `GET /health`, `GET /tasks/{id}`, `GET /executions/{id}` (stub), and audit-log endpoint.
- SQLModel async PostgreSQL models: `Task`, `Execution`, `Approval`, `AuditLog`.
- Deterministic state machine covering `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
- Embedded Policy Engine validating task contracts, project profiles, harness allowlist, and approval chain.
- Append-only `AuditService` wired into approvals and state transitions.
- `ApprovalService` as the single convergence point for all approvals.
- Plane Adapter interface + in-memory stub.
- Harness Provider Registry with OpenCode and Claude Code metadata.
- macro-agent executor abstraction (`MacroAgentClient`, `MacroAgentExecutor`) and `Execution` model.
- Automatic execution trigger after `EXECUTION` approval, with `READY → RUNNING` transition.
- opentasks materialization stub with DAG validation.
- In-process macro-agent Event Bridge translating workspace events into Controller state updates.
- Dockerfile and Docker Compose for local API + PostgreSQL.
- CLI (`approve`) and Telegram adapter stubs converging on `POST /approvals`.
- Verification stub, permission stub, and git-cascade landing stub.
- End-to-end Phase 1 smoke test.

Test coverage: **116 passed**, ruff clean, 1 pre-existing Pydantic `class-based config` deprecation warning.

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
   - Wire `PermissionService` into `ApprovalService`/API.
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
