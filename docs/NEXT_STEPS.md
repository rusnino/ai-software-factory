# Next Steps

## Current State

**REVIEW-017 independently re-verified REVIEW-016's fixes against a live PostgreSQL container and found one, `GAP-080`, does not actually hold: the HIGH-severity gate is open again.**

Genuinely fixed and independently re-verified in REVIEW-017 (not just a diff read):
- `GAP-078` (CRITICAL): every `datetime` column now uses `DateTime(timezone=True)`; live-verified `POST /tasks` succeeds against a real Postgres container with correctly-typed, correctly-offset persisted timestamps.
- `GAP-079` (HIGH): project-profile creation now uses dialect-aware `INSERT ... ON CONFLICT DO NOTHING` + re-fetch; live-verified with a genuinely forced two-connection race against real Postgres — exactly one profile row, both tasks persisted.
- `GAP-074` (HIGH): `PolicyEngine` now unions `contract.forbidden_paths` with `profile.security.forbidden_paths` (both directions), and `VerificationService` checks the union regardless of `CompletionContract` presence; live-reproduced closing the original silent-drop bypass.
- `GAP-073` (MEDIUM): body-size limit middleware now applies to `/events`, `/tasks`, and `/approvals`, and genuinely counts bytes read from the stream (not just the `Content-Length` header) — live-reproduced against all three routes with both the no-header and lying-header bypass angles.

**Not genuinely fixed — reopened:**
- `GAP-080` (HIGH): the explicit `get_by_id_for_update()`/`SELECT ... FOR UPDATE` call was removed from `api/approvals.py`, but `_trigger_execution` still holds an ordinary MVCC row lock across the live macro-agent HTTP call, because the `READY` transition's `UPDATE` is never committed before that call. REVIEW-017 live-reproduced the same ~3.5s block REVIEW-016 originally measured, now via the natural transactional lock instead of the explicit one. See `reviews/GAPS.md` for the fix shape needed (commit or flush-and-commit the `READY` transition before calling `executor.start()`).

Also open: `GAP-077` (MEDIUM, SPEC-09 §9.6 verification retry — fails closed, intentionally deferred) and three new REVIEW-017 findings — `GAP-081` (a new `RISK-17` row added documenting the SQLite-vs-Postgres dialect-parity defect class), `GAP-082` (LOW, orphaned `get_by_id_for_update()`), `GAP-083` (LOW, unconfigured/untunable DB connection-pool settings).

Test status: **202 passed**, `ruff` clean, `mypy --strict` clean. The suite now genuinely runs against real PostgreSQL too via `GC_TEST_DATABASE_URL` (confirmed not a silent SQLite fallback in REVIEW-017), though this path is opt-in with no CI enforcing it — see `RISK-17`.

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

10. **Verification failure retry (SPEC-09 §9.6)**
    - Currently unimplemented: `VerificationService.verify_and_advance()` transitions any failed
      verification straight to `FAILED`, with no retry-count tracking (`Task` has no attempt/retry
      field) and no failure-feedback call to macro-agent. Fails closed (never a false `DONE`), so not
      a governance defect, but SPEC-09 §9.6 specifies a retry-before-FAILED mechanism that doesn't
      exist yet — tracked as `GAP-077` (MEDIUM).

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
