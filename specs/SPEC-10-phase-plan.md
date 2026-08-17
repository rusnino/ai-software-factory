# SPEC-10: Phase Plan and Acceptance Criteria

## 10.1 Phase 1 — Core Governance + macro-agent PoC

Goal: prove Governance Controller can authorize and launch macro-agent with at least two distinct harnesses.

### Estimated Effort

- **Governance Controller** is the largest Phase 1 component.
- Estimated: **3.5–5 months** of one senior backend engineer, or **6–9 weeks** with two engineers working in parallel on clearly separated modules (state machine/approvals, Plane adapter, macro-agent integration, Event Bridge, harness provider registry).
- Estimate assumes ~4300–7000 lines of production Python code and a comparable volume of tests.

### Deliverables

1. Governance Controller service (FastAPI + SQLAlchemy + PostgreSQL).
2. State machine and single authoritative `POST /approvals` endpoint.
3. Task Contract v1 and Project Profile v1.
4. Plane adapter interface and in-memory/test-only stub, ready for Phase 2 integration.
5. macro-agent integration (start/status/cancel/collect).
6. Event Bridge (workspace events -> Controller with idempotency and retry).
7. Provider registry for OpenCode and at least one other harness.
8. Heterogeneous team PoC: tiny repo, `/health` endpoint + tests.
9. E2E test: create -> approve -> READY -> execute -> verify -> HUMAN_REVIEW -> DONE.

### Phase 1 Acceptance Criteria

- [ ] Controller stores task independently of macro-agent.
- [ ] Task cannot execute without durable human approval.
- [ ] Controller starts macro-agent only after policy check.
- [ ] Controller correlates its execution ID with macro-agent run/team IDs.
- [ ] At least two distinct agent harnesses participate in one execution.
- [ ] OpenCode is tested unless documented ACP blocker.
- [ ] Per-role harness selection is configuration-driven.
- [ ] Required macro-agent MCP tools work from non-Claude harness.
- [ ] git-cascade worktree/stream flow works.
- [ ] Agent messaging works where topology needs it.
- [ ] Reviewer can issue a verdict.
- [ ] Failed verification never produces DONE.
- [ ] Human review required before final DONE/merge.
- [ ] Phase 1 has no runtime dependency on Plane or Macro UI (E2E acceptance uses direct `POST /approvals` calls, not Plane webhooks).
- [ ] Governance logic remains outside macro-agent fork.

### Stop Condition

If OpenCode/Codex cannot receive required macro-agent MCP tools without invasive changes, stop and document blocker.

## 10.2 Phase 2 — Plane UI + Meta Orchestrator + OPA

- Deploy Plane CE.
- Build Plane adapter for bidirectional sync.
- Meta Orch integration: OpenCode + BMAD + OpenSpec.
- Intake adapter (Telegram/Email/API).
- Idea Ingestion Service.
- Human Triage queue in Plane.
- Optional: replace embedded Policy Engine with Open Policy Agent (OPA) as backend; Controller retains state machine, approval store, and audit log.

## 10.3 Phase 3 — Hardening and Runtime Diversity

- Docker sandboxing.
- Full harness matrix (Claude Code, Codex, Aider).
- Advanced conflict recovery.
- Project Profiles per repo.
- Semantic Reviewer.
- Better Completion Contract.
- Optional: introduce Temporal inside Execution Orchestration layer for durable long-running execution workflows (not as replacement for Governance Controller); revisit only if proven need exists.

## 10.4 Phase 4 — Production Hardening

- Firecracker/Kata isolation.
- Secret scoping (Vault/Doppler).
- Observability (metrics, alerts).
- Backups and disaster recovery.
- Distributed workers (only if needed).

## 10.5 Explicitly Postponed

- Kubernetes
- Redis unless justified
- Vector DB
- Telegram/Matrix as core communication
- HA before validation
- Dozens of agent profiles
- Nested subagent orchestration
- Windmill as core controller
- macro-inc/macro as PM
- Temporal as Governance Controller core (allowed only in Phase 3+ inside Execution layer)
- Replacing entire Policy Engine with OPA before Phase 2
