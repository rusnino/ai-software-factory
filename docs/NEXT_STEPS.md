# Next Steps

## Current State

Design package is complete and committed. The architecture is defined:

- Governance Controller as authoritative Python/FastAPI service.
- Plane CE as human-facing projection.
- macro-agent as execution orchestration layer.
- OpenCode as primary harness, with optional Claude Code / Codex / Aider.
- Event Bridge for workspace events.
- opentasks for runtime task graph.

## Immediate Next Step: Phase 1 Governance Controller

Priority: build the minimal Governance Controller that can accept an approval, enforce policy, and transition state.

### Tasks

1. **Scaffold repository**
   - Create `src/governance_controller/` Python project.
   - Use `uv` for dependency management.
   - Structure: `app/`, `models/`, `services/`, `api/`, `config/`, `tests/`.

2. **Database models (SQLModel)**
   - `Task`
   - `Execution`
   - `Approval`
   - `ProjectProfile`
   - `AuditLog`

3. **State machine**
   - Implement `PROPOSED → PLAN_APPROVED → EXEC_APPROVED → READY → RUNNING → AGENT_REVIEW → HUMAN_REVIEW → DONE / FAILED / BLOCKED`.
   - Forbid invalid transitions.

4. **Single approval endpoint**
   - `POST /approvals` with idempotency.
   - Support `approval_type`: plan, execution, merge.
   - Validate Task Contract and Project Profile before recording approval.

5. **Policy Engine (embedded Python for Phase 1)**
   - Task Contract completeness.
   - Project Profile constraints.
   - Harness allowlist.
   - Approval chain correctness.

6. **Audit log**
   - Append-only log of approvals, state changes, policy checks.

7. **Plane adapter stub**
   - Define interface.
   - Provide in-memory/test-only implementation.
   - Real Plane integration is Phase 2.

8. **Tests**
   - Unit tests for state machine.
   - Unit tests for approval endpoint idempotency.
   - Unit tests for Policy Engine.

## Blockers to Watch

- macro-agent API stability.
- OpenCode ACP compatibility with macro-agent MCP tools.

## Deferred to Phase 2

- Plane CE deployment and bidirectional sync.
- Meta Orchestrator integration.
- Intake Adapter.
- Open Policy Agent (OPA) as Policy Engine backend.
- Advanced isolation (Docker / Firecracker).

## Deferred to Phase 3+

- Temporal for durable execution workflows.
- Full harness matrix (Claude Code, Codex, Aider).
- Semantic Reviewer.
- Production hardening.
