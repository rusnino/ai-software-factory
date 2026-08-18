# Next Steps

## Current State

**The `CRITICAL`/`HIGH` gate is open: REVIEW-019 found `GAP-077`'s fix has three independent problems, plus two new, unrelated HIGH findings.**

`GAP-077` (retry-before-`FAILED`, SPEC-09 §9.6): a fix (`ee80cd6`) got the core retry math right (boundary condition, CAS safety, policy-capped `max_retries`) but:
1. Wired the new `FAILED -> RUNNING` transition into `StateMachine`'s *global* transition table instead of scoping it to the verification-retry call site — `EventBridge.handle()`'s generic event validation shares that table, so 6 of its 9 macro-agent event types can silently resurrect *any* terminally-`FAILED` task, with no attempt bound, no distinguishing audit trail, no human alert.
2. Replaying the same `landing:completed` event burns a retry attempt with no real new macro-agent execution (the processed-event dedup key isn't recorded on the retry path).
3. (`GAP-085`, the most severe) **the "retry" never actually retries**: `verify_and_advance()`'s retry branch never calls `MacroAgentExecutor.start()` — confirmed the only call site in the whole codebase is inside the approval flow, unreachable once a task leaves it. A "retried" task is flipped to `RUNNING` and permanently orphaned there — no execution ever restarts, no event will ever arrive to move it further, and no API route can recover it. This is worse than the pre-fix behavior (straight to `FAILED`, at least terminal and actionable).

Two new, unrelated HIGH findings from this round's fresh sweep:
- `GAP-086`: the `GAP-062`/`073` body-size-limit middleware's drain loop spins forever (100% CPU, no response ever sent) if a client disconnects mid-stream after exceeding the cap, instead of finishing the body — live-reproduced, a real DoS via any of `/tasks`/`/approvals`/`/events`.
- `GAP-087`: `POST /approvals`'s fallback idempotency key (used whenever no `Idempotency-Key` header is sent — the normal case) joins `task_id`/`actor` with `"|"` with no escaping; two unrelated tasks/actors can collide on this delimiter and produce an identical key, causing one approval to be silently dropped (reported to the caller as `"approved": true`) while the real target task never advances — live-reproduced.

Closed gaps, all independently re-verified by live reproduction against real PostgreSQL (not just diff review):
- `GAP-078` (CRITICAL): `DateTime(timezone=True)` on all datetime columns.
- `GAP-079` (HIGH): dialect-aware profile upsert.
- `GAP-074` (HIGH): `TaskContract.forbidden_paths` enforced.
- `GAP-073` (MEDIUM): body-size limit on all write endpoints.
- `GAP-080` (HIGH): no DB row lock held across macro-agent HTTP call — verified with a real lock-timing measurement, not just a "commit was called" unit-test check.
- `GAP-082` (LOW): orphaned `get_by_id_for_update()` removed (confirmed absent repo-wide).
- `GAP-083` (LOW): DB pool settings now configurable, confirmed genuinely wired by inspecting the instantiated engine's pool.
- `GAP-084` (MEDIUM): the 6 `test_db.py` tests that bypassed `GC_TEST_DATABASE_URL` now genuinely route through the shared dual-DB fixtures — confirmed by a bad-credentials run making exactly those 6 (and no others) fail with a real `asyncpg` auth error.

Still open, blocking the gate: `GAP-077`, `GAP-085`, `GAP-086`, `GAP-087` (all HIGH). Also open (non-blocking): `RISK-17`/`GAP-081` (SQLite-vs-Postgres dialect parity, no CI yet), `GAP-090` (MEDIUM — SPEC-09 §9.6's "alert human" step is entirely unimplemented, not just the macro-agent-feedback half), `GAP-091` (MEDIUM — DB pool-size/log-level settings accept invalid values silently, e.g. a negative pool size silently becomes unbounded).

Test status: **202 passed**, genuinely dual-DB now (`GAP-084` closed the last coverage bypass), `ruff` clean, `mypy --strict` clean. Passing tests do not cover `GAP-077`'s two regressions above — neither would be caught by the checked-in suite.

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
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors.
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

10. **Verification failure feedback to macro-agent, and actually restarting execution (SPEC-09 §9.6 steps 1-2)**
    - `GAP-077`'s retry loop is not safe to ship yet — see `reviews/GAPS.md`/the Current State section
      above (`GAP-077`, `GAP-085`, `GAP-086`, `GAP-087` all `OPEN`, `HIGH`). Besides emitting a real
      failure-feedback message to macro-agent, the retry path itself needs to actually restart
      execution (call `MacroAgentExecutor.start()`, mirroring `_trigger_execution`) and the
      `FAILED -> RUNNING` transition needs to be scoped so only the verification-retry path can use
      it, not every macro-agent event type.

11. **Alert human on terminal FAILED (SPEC-09 §9.6 step 3)**
    - Entirely unimplemented: no notification/alerting mechanism exists anywhere (no webhook, email,
      or Plane comment push). A terminally `FAILED` task is only discoverable by polling
      `GET /tasks/{id}` or the audit log. Tracked as `GAP-090`.

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
