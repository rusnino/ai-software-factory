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

- [x] Controller stores task independently of macro-agent. [^phase1-durability]
- [x] Task cannot execute without durable human approval. [^phase1-durability]
- [x] Controller starts macro-agent only after policy check. [^phase1-policy-bypass]
- [x] Controller correlates its execution ID with macro-agent run/team IDs.
- [ ] At least two distinct agent harnesses participate in one execution. [^phase1-harnesses]
- [ ] OpenCode is tested unless documented ACP blocker. [^phase1-harnesses]
- [x] Per-role harness selection is configuration-driven. [^phase1-harnesses]
- [ ] Required macro-agent MCP tools work from non-Claude harness. [^phase1-mcp]
- [ ] git-cascade worktree/stream flow works. [^phase1-git-cascade]
- [ ] Agent messaging works where topology needs it. [^phase1-messaging]
- [ ] Reviewer can issue a verdict. [^phase1-reviewer]
- [x] Failed verification never produces DONE. [^phase1-verification]
- [x] Human review required before final DONE/merge.
- [x] Phase 1 has no runtime dependency on Plane or Macro UI (E2E acceptance uses direct `POST /approvals` calls, not Plane webhooks).
- [x] Governance logic remains outside macro-agent fork.

[^phase1-harnesses]: Phase 1 PoC registers OpenCode and Claude Code in the provider registry, but real multi-harness execution and per-role selection require macro-agent runtime integration planned for Phase 2.
[^phase1-mcp]: OpenCode ACP/MCP compatibility with macro-agent tools is a documented Phase 1 stop-condition risk; no invasive testing was performed and no blocker was encountered for the stub path.
[^phase1-git-cascade]: The `GitCascadeService` landing stub referenced here was removed as orphaned dead code when GAP-029/030 were closed (`0920b65`) — no landing stub of any kind exists today. Real git worktree/stream flow and cascade landing require git integration deferred to Phase 2/3.
[^phase1-messaging]: Agent messaging is out of scope for the Phase 1 Governance Controller PoC; handled by macro-agent runtime.
[^phase1-reviewer]: Reviewer verdict workflow is deferred to Phase 3 (Semantic Reviewer). Phase 1 enforces HUMAN_REVIEW gate before DONE.
[^phase1-verification]: `VerificationService` is invoked by `EventBridge.handle()` on `landing:completed`, executes `required`/`optional` `Check` commands via real subprocess with real exit-code comparison, and gates `AGENT_REVIEW -> HUMAN_REVIEW` vs `FAILED` on the result. Both the failing and passing paths are covered by automated tests (GAP-006, GAP-021 closed). `GAP-058` (HIGH, commit-before-raise in `verify_and_advance()`) and `GAP-074` (HIGH, `TaskContract.forbidden_paths` silently dropped when a `CompletionContract` is attached) are both `CLOSED` per `reviews/GAPS.md` — `GAP-074`'s fix (`1cb5a44`/`d31ebd4`) was independently live-reproduced as a genuine union-of-both-sources fix in REVIEW-017, not just a diff read. REVIEW-019 found this checklist item's guarantee was threatened by `GAP-077`'s first fix attempt (a `FAILED -> RUNNING` edge added to `StateMachine`'s shared global transition table, letting 6 unrelated event types resurrect a terminally-`FAILED` task) and `GAP-085` (the "retry" never actually re-invoking the macro-agent). REVIEW-020 confirmed both are now genuinely fixed: the transition table no longer contains that edge at all, and the retry path genuinely restarts execution. `GAP-077` remains `OPEN` for one narrower, distinct residual — a replayed `landing:completed` event re-enters the retry path and triggers a second real execution — which does not by itself threaten this checklist item's guarantee (it causes a duplicate execution attempt, not a bypass of the `FAILED`/`DONE` boundary), so this item stays checked.
[^phase1-durability]: `get_db()` commits per-request sessions on success and rolls back on exception (GAP-022 closed). Audit rows for rejected approvals are committed before the rejection response is returned (GAP-044 closed). REVIEW-016 found every `datetime` column was `TIMESTAMP WITHOUT TIME ZONE` while every `utc_now()` call produced a timezone-aware value, which `asyncpg` rejects outright — live-reproduced as `POST /tasks` failing on its very first write against real Postgres. `GAP-078` (CRITICAL, storage didn't work against Postgres at all) and `GAP-079` (HIGH, TOCTOU race silently dropping a task) are both `CLOSED`, independently re-verified against a live Postgres container in REVIEW-017 — checked above on that basis. `GAP-080` (HIGH, a lock held across the live macro-agent call) is **not** genuinely fixed and is `OPEN` again per `reviews/GAPS.md`: the explicit `FOR UPDATE` call was removed, but REVIEW-017 found `_trigger_execution` still holds the row lock for the same duration via its own uncommitted transaction, live-reproduced with the same ~3.5s block REVIEW-016 originally measured. This is a fail-fast/concurrency-robustness defect, not a storage-durability one, so it doesn't reopen these two specific checklist items — but it does keep the overall Phase 1 gate closed per `reviews/GAPS.md`'s rule.
[^phase1-policy-bypass]: `GAP-057` (CRITICAL, forbidden-path traversal bypass) and `GAP-074` (HIGH, `TaskContract.forbidden_paths` never read by `PolicyEngine`) are both `CLOSED` per `reviews/GAPS.md` — see `[^phase1-verification]`.

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
