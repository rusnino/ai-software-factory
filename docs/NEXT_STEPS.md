# Next Steps

## Current State

**REVIEW-024 independently re-verified REVIEW-023's fixes and found 2 of them incomplete — the `CRITICAL`/`HIGH` gate is open again on `GAP-095` and `GAP-097`.**

- `GAP-095` — **PARTIALLY FIXED, reopened.** The row-lock half is genuinely fixed (`96357af`'s commit before `verify_and_advance()`, live-verified against real Postgres: a concurrent write now returns in 0.04s instead of blocking for the verification's full duration). The timeout half does not work: `_run_check`'s `except TimeoutError` branch calls `proc.kill()`, which only kills the `/bin/sh` wrapper `asyncio.create_subprocess_shell` spawns, not any child process the shell forks to run the actual command — live-reproduced, `sleep 300` with `timeout=3.0` returned only at 300s, and a non-exiting command (`cat`) hung indefinitely. The checked-in test never asserts on elapsed time, so it passes without proving the timeout fires.
- `GAP-096` (`cda9a91`, confirmed genuinely fixed): rejects any actor starting with `system:`/`agent:`, matching SPEC-03's structured identifiers, without over-blocking legitimate actors like `agentsmith@example.com`.
- `GAP-097` — **PARTIALLY FIXED, reopened.** The dedup key is now written in a `finally` block around `verify_and_advance()` — but never committed there, so if the original exception continues propagating (which it does, on an `executor.start()` failure), `get_db()`'s own exception handler rolls back the whole session including that `finally`-block write. Live-reproduced: after a mocked `executor.start()` failure, `processedevent` had 0 rows immediately afterward; a second reproduction (an earlier-stage exception) showed the unmasked double-processing this was supposed to prevent. Same "write-then-raise without an intervening commit" pattern as `GAP-044`/`054`/`058`. Also flagged: moving straight to terminal `FAILED` on any `executor.start()` failure forfeits the entire remaining retry budget on the first transient blip, not just when retries are exhausted — worth a second look as a design question, not necessarily a bug.
- `GAP-098`/`099`/`100`/`101`/`103`/`104`/`106` (confirmed genuinely fixed) and `GAP-105` (backfilled directly in REVIEW-024, reversing an inconsistent `ACCEPTED` disposition — see `reviews/GAPS.md`).

Test status: **223 passed** on SQLite and PostgreSQL, `ruff` clean, `mypy --strict` clean — none of this catches `GAP-095`/`GAP-097`'s residuals.

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
- `PermissionService` wired into `ApprovalService` to reject self-approval and system/agent actors
  (now also rejects `system:*` / `agent:*` structured identifiers as of `GAP-096`).
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
    - `GAP-077`'s retry loop genuinely retries and restarts execution (`GAP-077`, `GAP-085` both
      `CLOSED`, independently re-verified). The remaining gap: no failure-feedback message is emitted
      to macro-agent so it can repair and re-land; `verification_service.py` still has a `# TODO`
      marking this. `GAP-097` is now `CLOSED`: a transient `executor.start()` failure records the
      dedup key and moves the task to terminal `FAILED`, preventing double-consumption of the retry
      budget on SPEC-05 §5.4 redelivery.

11. **Real outbound alert channel for terminal FAILED (SPEC-09 §9.6 step 3)**
    - `GAP-090` added a real, tested `alert_human` audit-log marker — a human can now find terminal
      failures by querying the audit log, but there's still no webhook/email/Plane-comment push; a
      human must still poll to discover the failure.

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
