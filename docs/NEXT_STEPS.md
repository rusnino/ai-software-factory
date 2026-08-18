# Next Steps

## Current State

**The `CRITICAL`/`HIGH` gate is open on one narrow, well-understood residual: everything else REVIEW-019 found is now genuinely closed.**

`GAP-077`/`GAP-085` (retry-before-`FAILED`, SPEC-09 §9.6) had three bugs across two fix attempts. `f62c5ba` genuinely fixed two of them, independently live-verified in REVIEW-020:
- `GAP-085` (was the most severe): the retry now genuinely restarts execution — `_start_retry_execution()` creates a real `Execution` row and calls `executor.start()`, confirmed by a full round trip through `EventBridge.handle()` and an exact `max_retries` boundary check.
- Global transition-table scope: `FAILED -> RUNNING` is no longer in `StateMachine`'s shared table at all; a dedicated `atomic_transition_from_failed_to_running()` is its only call site — confirmed all 6 previously-dangerous event types are now correctly rejected against a `FAILED` task, no resurrection.
- No `GAP-080`-class regression: the retry's new DB writes commit before the live `executor.start()` call (probe returns in 0.02s against real Postgres), and the CAS is safe under a forced two-session race.

**Still open, blocking the gate**: `GAP-077` itself, narrowed to its one remaining bug — event replay. `EventBridge`'s early-return still skips recording the processed-event dedup key on a retry, so replaying the identical `landing:completed` event re-enters the retry path. This is now **worse than originally reported**: before `GAP-085`'s fix this only double-incremented a counter with no real effect; now it causes a genuine **second live macro-agent execution** for one event. Fix direction: record the dedup key for the retry path too.

Closed gaps, all independently re-verified by live reproduction (not just diff review):
- `GAP-078` (CRITICAL): `DateTime(timezone=True)` on all datetime columns.
- `GAP-079` (HIGH): dialect-aware profile upsert.
- `GAP-074` (HIGH): `TaskContract.forbidden_paths` enforced.
- `GAP-073` (MEDIUM): body-size limit on all write endpoints.
- `GAP-080` (HIGH): no DB row lock held across macro-agent HTTP call.
- `GAP-085` (HIGH): retry genuinely restarts execution.
- `GAP-086` (HIGH): the body-size-limit middleware's drain loop no longer spins forever on client disconnect — confirmed via before/after reproduction (reverted code spun >1M `receive()` calls in 3s; current code returns promptly).
- `GAP-087` (HIGH): the approvals fallback idempotency key is now a `sha256` hash, not a raw delimiter-joined string — confirmed the original cross-task collision no longer occurs, through the real ASGI app and DB.
- `GAP-082`/`GAP-083` (LOW): orphaned dead code removed; DB pool settings configurable and genuinely wired.
- `GAP-084` (MEDIUM): dual-DB test coverage bypass fixed, confirmed genuine.
- `GAP-090` (MEDIUM): SPEC-09 §9.6's "alert human" now has a real, tested audit-log marker (`alert_human` event) — honestly scoped as not a real outbound notification (that remains Phase 2 work, see candidate task 11).

Still open (non-blocking): `RISK-17`/`GAP-081` (SQLite-vs-Postgres dialect parity, no CI yet); `GAP-091` (MEDIUM, reopened — the `GAP-083` pool-setting validation fix missed `pool_size=0`, which reproduces the same unbounded-pool bug as a negative value, and never added a validator for `pool_timeout` at all); `GAP-092` (LOW — a recurring hygiene issue where the coding agent's ledger edits cite commit hashes that aren't actually reachable from `main`, now happened twice).

Test status: **209 passed**, `ruff` clean, `mypy --strict` clean. `GAP-077`'s remaining event-replay bug has no test coverage.

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
