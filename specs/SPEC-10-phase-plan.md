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

**`REVIEW-004` found that `session.commit()` is never called anywhere in `governance_controller` — every
write is discarded when the per-request DB session closes (see `reviews/GAPS.md` GAP-022, CRITICAL). Every
checked item below describes correct *in-request* logic; none of it survives past one HTTP request against
real PostgreSQL today, so treat every `[x]` here as provisional until GAP-022 closes.**

- [ ] Controller stores task independently of macro-agent. [^phase1-durability]
- [ ] Task cannot execute without durable human approval. [^phase1-durability]
- [x] Controller starts macro-agent only after policy check.
- [x] Controller correlates its execution ID with macro-agent run/team IDs.
- [ ] At least two distinct agent harnesses participate in one execution. [^phase1-harnesses]
- [ ] OpenCode is tested unless documented ACP blocker. [^phase1-harnesses]
- [ ] Per-role harness selection is configuration-driven. [^phase1-harnesses]
- [ ] Required macro-agent MCP tools work from non-Claude harness. [^phase1-mcp]
- [ ] git-cascade worktree/stream flow works. [^phase1-git-cascade]
- [ ] Agent messaging works where topology needs it. [^phase1-messaging]
- [ ] Reviewer can issue a verdict. [^phase1-reviewer]
- [ ] Failed verification never produces DONE. [^phase1-verification]
- [x] Human review required before final DONE/merge.
- [x] Phase 1 has no runtime dependency on Plane or Macro UI (E2E acceptance uses direct `POST /approvals` calls, not Plane webhooks).
- [x] Governance logic remains outside macro-agent fork.

[^phase1-harnesses]: Phase 1 PoC registers OpenCode and Claude Code in the provider registry, but real multi-harness execution and per-role selection require macro-agent runtime integration planned for Phase 2.
[^phase1-mcp]: OpenCode ACP/MCP compatibility with macro-agent tools is a documented Phase 1 stop-condition risk; no invasive testing was performed and no blocker was encountered for the stub path.
[^phase1-git-cascade]: A deterministic landing stub with branch validation exists. Real git worktree/stream flow and cascade landing require git integration deferred to Phase 2/3.
[^phase1-messaging]: Agent messaging is out of scope for the Phase 1 Governance Controller PoC; handled by macro-agent runtime.
[^phase1-reviewer]: Reviewer verdict workflow is deferred to Phase 3 (Semantic Reviewer). Phase 1 enforces HUMAN_REVIEW gate before DONE.
[^phase1-verification]: Updated by `REVIEW-004`: `VerificationService` is now genuinely invoked by `EventBridge.handle()` on `landing:completed`, executes `required`/`optional` `Check` commands via real subprocess with real exit-code comparison, and gates `AGENT_REVIEW -> HUMAN_REVIEW` vs `FAILED` on the result (GAP-006, closed, confirmed by live reproduction in `reviews/REVIEW-003-round2-gap-verification.md`). Left unchecked because (a) no automated test exercises this specific integration path (GAP-021, open) and (b) `VerificationService`'s own `forbidden_path_check` still has a subpath-matching bug (GAP-020, open).
[^phase1-durability]: `REVIEW-004` (`reviews/GAPS.md` GAP-022, CRITICAL): `session.commit()` is never called anywhere in the codebase, so no `Task`, `Approval`, or `AuditLog` row survives past the request that created it against real PostgreSQL. Masked in tests because the test fixture shares one already-open transaction across all requests in a test. Both items above will be re-checkable once GAP-022 closes.

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
