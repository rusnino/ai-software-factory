# Next Steps

## Current State

**All `CRITICAL`/`HIGH` gaps are closed.** `GAP-080` was reopened in REVIEW-017 because the original fix only removed the explicit `FOR UPDATE` but still held an MVCC row lock across the live macro-agent HTTP call. REVIEW-018 independently live-verified the second fix attempt against real Postgres with the same lock-timing method that caught the first one: the probe now returns in ~0.02s instead of blocking ~3.5s — genuinely fixed this time.

Closed gaps, all independently re-verified by live reproduction against real PostgreSQL in REVIEW-017/018 (not just diff review):
- `GAP-078` (CRITICAL): `DateTime(timezone=True)` on all datetime columns.
- `GAP-079` (HIGH): dialect-aware profile upsert.
- `GAP-074` (HIGH): `TaskContract.forbidden_paths` enforced.
- `GAP-073` (MEDIUM): body-size limit on all write endpoints.
- `GAP-080` (HIGH): no DB row lock held across macro-agent HTTP call — verified with a real lock-timing measurement, not just a "commit was called" unit-test check.
- `GAP-082` (LOW): orphaned `get_by_id_for_update()` removed (confirmed absent repo-wide).
- `GAP-083` (LOW): DB pool settings now configurable via `GC_DATABASE_POOL_SIZE`/`GC_DATABASE_MAX_OVERFLOW`/`GC_DATABASE_POOL_TIMEOUT`/`GC_DATABASE_POOL_PRE_PING` — confirmed genuinely wired by inspecting the instantiated engine's pool, not just declared.

Still open: `RISK-17`/`GAP-081` (SQLite-vs-Postgres dialect parity, no CI yet). `GAP-077` (MEDIUM, SPEC-09 §9.6 verification retry) is now implemented with `Task.execution_attempts` and a `FAILED -> RUNNING` retry loop; the failure-feedback call to macro-agent remains a Phase 2 integration TODO. `GAP-084` (MEDIUM, dual-DB test-coverage bypass in `test_db.py`) is closed.

**Test status: 202 passed on both SQLite and real PostgreSQL — but "full coverage on both databases" is not quite accurate.** A full audit (`GAP-084`) found 6 of `tests/test_db.py`'s tests — covering `get_db()`'s commit/rollback branch logic, the same historically fragile area behind `RISK-16` — use a hardcoded local SQLite fixture that never reads `GC_TEST_DATABASE_URL` at all, so they've never actually run against real Postgres transaction semantics. The other 76 DB-integration tests are genuinely dual-DB (confirmed: pointing `GC_TEST_DATABASE_URL` at a Postgres URL with bad credentials makes exactly those 76 — not these 6 — fail with a real `asyncpg` auth error). `ruff` clean, `mypy --strict` clean.

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

10. **Verification failure feedback to macro-agent (SPEC-09 §9.6 step 2)**
    - `GAP-077`'s retry loop is implemented: failed verification transitions `FAILED -> RUNNING`
      while `execution_attempts < max_retries`. The remaining Phase 2 work is to emit a real
      failure-feedback message to macro-agent so it can repair and re-land.

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
