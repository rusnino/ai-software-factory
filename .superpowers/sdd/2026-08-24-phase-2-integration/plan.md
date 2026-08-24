# Phase 2 — Plane CE + macro-agent Integration

**Goal:** Connect the Governance Controller to Plane CE for human-facing task projection and to macro-agent for real execution, completing the Phase 1→2 handoff.

**Architecture:** Extend the existing FastAPI Controller with real HTTP adapters. Plane Adapter becomes a REST client that projects state to Plane and receives webhooks. macro-agent integration replaces the stub executor with a separate Node.js service wrapper that exposes REST endpoints and forwards workspace events to Controller `POST /events`. Each subsystem is built behind existing abstraction interfaces.

**Local Dev Environment:**
- Plane CE: `http://127.0.0.1:8081`, workspace `ai-factory`, project `4e8e52d0-1779-41e7-8c67-d256d48b1654`, token `dev-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
- macro-agent: will be installed under `src/macro_agent_service/` when Task 4 starts.

**Test Discipline:** Every task ends with passing tests on SQLite and PostgreSQL plus `ruff`/`mypy governance_controller` clean. Contract tests against real Plane CE use the local instance.

---

## Task 1: Real Plane CE HTTP Client

Implement `PlaneClient` with methods to read/write Plane tasks, comments, dependencies.

**Files:**
- Create: `src/governance_controller/governance_controller/adapters/plane_client.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_plane_client.py`

- [x] Step 1: Add Plane config fields (`plane_base_url`, `plane_api_token`, `plane_workspace_slug`, `plane_project_id`) to `Settings`.
- [x] Step 2: Implement `PlaneClient` with `httpx.AsyncClient`, auth header, timeout.
- [x] Step 3: Implement methods: `get_task`, `list_tasks`, `update_task_state`, `add_comment`, `create_task`, `get_task_dependencies`.
- [x] Step 4: Write unit tests with `pytest-httpx` mocks.
- [x] Step 5: Write contract tests against real local Plane CE verifying CRUD round-trip.
- [x] Step 6: Run tests and commit.

## Task 2: Plane Webhook Receiver

Add `POST /webhooks/plane` endpoint that translates Plane events into Controller approvals per SPEC-04 §4.3a.

**Files:**
- Create: `src/governance_controller/governance_controller/api/webhooks.py`
- Modify: `src/governance_controller/governance_controller/main.py`
- Test: `src/governance_controller/tests/test_plane_webhook.py`

- [x] Step 1: Define Pydantic `PlaneWebhookEvent` schema.
- [x] Step 2: Add route `POST /webhooks/plane`.
- [x] Step 3: Implement translation rules: human actor, eligible state transition, non-bulk, policy validation, stale-state rejection.
- [x] Step 4: On rejection, revert Plane state via `PlaneClient.add_comment`.
- [x] Step 5: Tests for valid plan/execution/merge transitions and all rejection paths.
- [x] Step 6: Run tests and commit.

## Task 3: Controller → Plane Projection Service

Service that syncs Controller `TaskState` to Plane execution-status fields and comments on failures.

**Files:**
- Create: `src/governance_controller/governance_controller/services/plane_projection_service.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`, `services/task_service.py`, `adapters/macro_agent/event_bridge.py`
- Test: `src/governance_controller/tests/test_plane_projection_service.py`

- [x] Step 1: Implement `PlaneProjectionService.sync_task_state(task_id, state)`.
- [x] Step 2: Map Controller states to Plane states per SPEC-04 §4.5.
- [x] Step 3: Hook projection into successful state transitions behind config flag.
- [x] Step 4: Tests for all state mappings and failure-comment behavior.
- [x] Step 5: Run tests and commit.

## Task 4: macro-agent Service Scaffold

Install macro-agent in `src/macro_agent_service/` with a thin TypeScript wrapper exposing REST endpoints.

**Files:**
- Create: `src/macro_agent_service/package.json`
- Create: `src/macro_agent_service/src/index.ts`
- Create: `src/macro_agent_service/teams/self-driving/team.yaml`
- Create: `src/macro_agent_service/Dockerfile`
- Modify: `src/governance_controller/docker-compose.yml`

- [x] Step 1: `npm init` and `npm install macro-agent@latest` pinned exact.
- [x] Step 2: Implement wrapper that boots `bootV2`, exposes REST on port 4000, forwards `workspaceManager.onEvent` to Controller `POST /events`.
- [x] Step 3: Add minimal team YAML using OpenCode worker role.
- [x] Step 4: Verify wrapper starts and health endpoint responds.
- [x] Step 5: Commit service scaffold.

## Task 5: Real macro-agent Executor Client

Replace stub `MacroAgentClient` with real HTTP calls to macro-agent service.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/executor.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_macro_agent_client.py`

- [x] Step 1: Add `macro_agent_base_url`, `macro_agent_api_token` to Settings.
- [x] Step 2: Implement `start_run`, `status`, `cancel`, `collect` with httpx.
- [x] Step 3: Update `MacroAgentExecutor` to use real client methods.
- [x] Step 4: Mock tests for HTTP contract.
- [x] Step 5: Contract test against running macro-agent service (optional, manual gate).
- [x] Step 6: Run tests and commit.

## Task 6: opentasks Runtime DAG Materializer

Build runtime DAG from Plane dependencies and pass to macro-agent service.

**Files:**
- Create: `src/governance_controller/governance_controller/services/opentasks_materializer.py`
- Create: `src/governance_controller/governance_controller/schemas/opentasks.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`
- Test: `src/governance_controller/tests/test_opentasks_materializer.py`

- [x] Step 1: Implement DAG fetch from Plane dependencies.
- [x] Step 2: Define `OpentasksDAG` Pydantic schema with metadata.
- [x] Step 3: Wire materialization into `EXEC_APPROVED -> READY` transition.
- [x] Step 4: Tests for cycle detection and missing dependencies.
- [x] Step 5: Run tests and commit.

## Task 7: Reconciliation Job

Periodic comparison of Plane state vs Controller state with CLI command.

**Files:**
- Create: `src/governance_controller/governance_controller/services/reconciliation_service.py`
- Modify: `src/governance_controller/governance_controller/cli.py`
- Test: `src/governance_controller/tests/test_reconciliation_service.py`

- [x] Step 1: Implement divergence check.
- [x] Step 2: Implement reconcile projection fields only; alert on content drift for EXEC_APPROVED+.
- [x] Step 3: Add `reconcile` CLI command.
- [x] Step 4: Tests.
- [x] Step 5: Run tests and commit.

## Task 8: Intake Adapter (Telegram + Email)

Multi-channel intake normalizing to Plane drafts.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/telegram.py`
- Create: `src/governance_controller/governance_controller/adapters/email.py`
- Create: `src/governance_controller/governance_controller/services/idea_ingestion_service.py`
- Create: `src/governance_controller/governance_controller/api/intake.py`
- Test: `src/governance_controller/tests/test_intake.py`

- [x] Step 1: Implement `RawIdea` schema and simple classifier.
- [x] Step 2: Implement Telegram webhook handler with secret-token check.
- [x] Step 3: Implement email webhook handler.
- [x] Step 4: Wire `/intake/telegram` and `/intake/email` routes.
- [x] Step 5: Tests for classification and Plane draft creation.
- [x] Step 6: Run tests and commit.

## Task 9: Verification Failure Feedback + Terminal Alerting

Send failure feedback to macro-agent and real outbound alerts for terminal FAILED.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py`
- Create: `src/governance_controller/governance_controller/services/alert_service.py`
- Test: `src/governance_controller/tests/test_verification_feedback.py`, `tests/test_alert_service.py`

- [x] Step 1: Add `send_feedback` to `MacroAgentClient`.
- [x] Step 2: Call feedback before retry in `VerificationService`.
- [x] Step 3: Implement `AlertService` with Plane comment alert.
- [x] Step 4: Replace audit-only alert with real alert.
- [x] Step 5: Tests.
- [x] Step 6: Run tests and commit.

## Task 10: Optional OPA Backend Client

Optional policy engine backend via OPA.

**Files:**
- Create: `src/governance_controller/governance_controller/adapters/opa_client.py`
- Create: `src/governance_controller/governance_controller/services/policy_engine_backend.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_opa_client.py`

- [x] Step 1: Implement `OPAClient.evaluate`.
- [x] Step 2: Add backend switch (`PolicyEngineBackend`).
- [x] Step 3: Wire into `ApprovalService`.
- [x] Step 4: Tests.
- [x] Step 5: Run tests and commit.

## Final Verification

- [x] Full suite SQLite: `uv run pytest tests/ -q` → 351 passed, 5 skipped
- [x] Full suite PostgreSQL: `GC_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test uv run pytest tests/ -q` → 354 passed, 2 skipped
- [x] `uv run ruff check governance_controller tests && uv run mypy governance_controller` → clean
- [x] `uv run ruff check src/macro_agent_service tests && uv run mypy src/macro_agent_service` → clean
- [x] Update `docs/NEXT_STEPS.md` and `specs/SPEC-10-phase-plan.md` Phase 2 status.

## Post-Implementation Review

First review round (`3907f24`) found 13 issues, including 1 CRITICAL (`#154`) and 4 HIGH
(`#152`, `#155`, `#156`, `#157`). Phase 2 is implemented but **not gate-clean**. The issue tracker
is the authoritative source of remaining work.
