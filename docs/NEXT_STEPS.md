# Next Steps

## Current State

**`reviews/GAPS.md` has zero `OPEN`/`IN_PROGRESS` rows for the first time across this 22-review series.** `GAP-094` (the last blocking gap — SQLite's `StaticPool` rejecting the pool-tuning kwargs `GAP-083`/`GAP-091` added, breaking module import under the SQLite fallback path) is fixed and independently re-verified in REVIEW-022: import now succeeds under `GC_DATABASE_URL=sqlite+aiosqlite:///...`, a full end-to-end task create/commit works against SQLite, and the default Postgres config still receives real pool kwargs (no regression). This is the first time the `CRITICAL`/`HIGH` gate has been clean *and* stayed clean through an immediate re-check with nothing newly found in the process — every prior "clean" milestone in this series was reopened by the very next round's fresh-eyes pass.

`GAP-077` (event replay) and `GAP-091` (pool-setting validation gaps), closed in REVIEW-021 and independently re-verified: `EventBridge` now records the processed-event dedup key before returning on the retry path (a replayed identical event no longer double-executes, a genuinely distinct subsequent event still processes normally); `pool_size=0`/`max_overflow=0` are rejected like negative values, `pool_timeout` correctly rejects negative values while still accepting `0`. `GAP-093` (a suspected concurrency race) was analyzed and found not exploitable — the existing `atomic_transition` CAS already serializes concurrent identical event deliveries.

All other gaps remain closed, independently re-verified by live reproduction across the series (not just diff review) — see `reviews/GAPS.md` for the full 94-row ledger. Non-blocking, already-accepted items: `RISK-17`/`GAP-081` (SQLite-vs-Postgres dialect parity risk, documented, no CI enforcing the dual-DB test path yet) and `GAP-092` (a recurring dangling-commit-hash ledger-hygiene note, resolved once the coding agent started verifying hashes before writing them).

Test status: **212 passed**, `ruff` clean, `mypy --strict` clean, confirmed working against both SQLite and real PostgreSQL.

**This does not mean Phase 1 is feature-complete** — see "Immediate Next Step: Phase 2" below for the substantial, honestly-disclosed list of deferred/stubbed work (real Plane sync, real macro-agent integration, reconciliation, Meta Orchestrator, Intake Adapter, security hardening, SPEC-09 §9.6's real outbound alert channel, and more). It means the acceptance criteria and known-defect ledger for what *has* been built are, as of this review, genuinely clean.

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
