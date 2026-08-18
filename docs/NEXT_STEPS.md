# Next Steps

## Current State

**REVIEW-013 (a full fresh review, not just re-verification of prior findings) found 1 new `CRITICAL` and 4 new `HIGH` gaps still open — Phase 1 acceptance criteria are not currently met.** Per `reviews/GAPS.md`'s gate rule, this blocks any claim of Phase 1 completeness until they're closed:

- `GAP-057` (CRITICAL): `PolicyEngine` and `VerificationService`'s forbidden-path checks don't normalize `..` traversal, so a path like `src/../../.ssh/id_rsa` bypasses both the pre-approval policy gate and the post-execution verification gate.
- `GAP-058` (HIGH): `VerificationService.verify_and_advance()`'s CAS-failure branch raises without committing first — the same defect class `GAP-044` fixed in `approval_service.py`, unfixed in this sibling file.
- `GAP-059` (HIGH): `PolicyEngine` never checks `TaskContract.execution.timeout_minutes`/`max_retries` against `ProjectProfile.execution.timeout_minutes`.
- `GAP-060` (HIGH): `POST /tasks` leaks an unhandled 500 (raw `IntegrityError`) on a duplicate `task_id` instead of `409 Conflict`.
- `GAP-061` (HIGH): `uvicorn` is the Dockerfile's `CMD` but is absent from `pyproject.toml`/`uv.lock` — `docker compose up`'s API container cannot start. Live-reproduced.

See `reviews/REVIEW-013-full-fresh-review.md` for full detail, live-reproduction evidence, and the remaining `MEDIUM`/`LOW` findings (unauthenticated/unbounded `POST /events` ingestion, orphaned `opentasks_service.py`, and others).

`GAP-031` has been corrected to `CLOSED` in this round — its originally-reported data-loss defect was fixed by `GAP-044`'s commit (`3e93928`), confirmed independently by three reviewers; the ledger status was simply never updated. A distinct residual (uncaught `RuntimeError` from executor-start failure still surfaces as a bare 500) is now tracked separately as `GAP-064`.

Test status: **175 passed**, `ruff` clean, `mypy --strict` clean. (Tooling is clean; none of the new findings are tooling-detectable — they require live reproduction or manual code reading.)

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
   - Build a periodic job that compares live Plane state, Controller DB state, and opentasks runtime graph.
   - Detect divergence, raise human alert, and auto-correct only projection fields.
   - *Deferred to Phase 2 because both Plane and opentasks integrations are still stubs in Phase 1.*

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

## Blockers to Watch

- macro-agent API stability and `/runs` contract.
- OpenCode ACP compatibility with macro-agent MCP tools.
- Plane CE self-hosted availability and API rate limits.

## Deferred to Phase 3+

- Full harness matrix (Claude Code, Codex, Aider) with runtime selection.
- Meta Orchestrator integration — evaluate sudocode-ai/sudocode's Spec/Issue graph model and OpenSpec
  integration before building idea-decomposition tooling from scratch (its execution engine is out of
  scope; that's macro-agent's job) — see `docs/research-alexngai-ecosystem-and-sudocode.md`.
- Intake Adapter (non-Plane task ingestion).
- Semantic Reviewer.
- Production hardening (metrics, tracing, HA).
- Evaluate LongHorizon-Harness (or similar durable-execution wrappers) as an optional `AgentHarness`
  adapter for long-running/GUI-touching opentasks — see `docs/research-longhorizon-harness.md`.
- If macro-agent's pre-1.0 risk (RISK-02/08) ever materializes into a real blocker, alexngai/openswarm is a
  concrete alternative execution engine (untested API surface, verify before evaluating further);
  alexngai/openhive is a multi-swarm federation candidate once single-swarm operation is proven — see
  `docs/research-alexngai-ecosystem-and-sudocode.md`.
