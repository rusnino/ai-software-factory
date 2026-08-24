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

- [ ] Step 1: Add Plane config fields (`plane_base_url`, `plane_api_token`, `plane_workspace_slug`, `plane_project_id`) to `Settings`.
- [ ] Step 2: Implement `PlaneClient` with `httpx.AsyncClient`, auth header, timeout.
- [ ] Step 3: Implement methods: `get_task`, `list_tasks`, `update_task_state`, `add_comment`, `create_task`, `get_task_dependencies`.
- [ ] Step 4: Write unit tests with `pytest-httpx` mocks.
- [ ] Step 5: Write contract tests against real local Plane CE verifying CRUD round-trip.
- [ ] Step 6: Run tests and commit.

## Task 2: Plane Webhook Receiver

Add `POST /webhooks/plane` endpoint that translates Plane events into Controller approvals per SPEC-04 §4.3a.

**Files:**
- Create: `src/governance_controller/governance_controller/api/webhooks.py`
- Modify: `src/governance_controller/governance_controller/main.py`
- Test: `src/governance_controller/tests/test_plane_webhook.py`

- [ ] Step 1: Define Pydantic `PlaneWebhookEvent` schema.
- [ ] Step 2: Add route `POST /webhooks/plane`.
- [ ] Step 3: Implement translation rules: human actor, eligible state transition, non-bulk, policy validation, stale-state rejection.
- [ ] Step 4: On rejection, revert Plane state via `PlaneClient.add_comment`.
- [ ] Step 5: Tests for valid plan/execution/merge transitions and all rejection paths.
- [ ] Step 6: Run tests and commit.

## Task 3: Controller → Plane Projection Service

Service that syncs Controller `TaskState` to Plane execution-status fields and comments on failures.

**Files:**
- Create: `src/governance_controller/governance_controller/services/plane_projection_service.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`, `services/task_service.py`, `adapters/macro_agent/event_bridge.py`
- Test: `src/governance_controller/tests/test_plane_projection_service.py`

- [ ] Step 1: Implement `PlaneProjectionService.sync_task_state(task_id, state)`.
- [ ] Step 2: Map Controller states to Plane states per SPEC-04 §4.5.
- [ ] Step 3: Hook projection into successful state transitions behind config flag.
- [ ] Step 4: Tests for all state mappings and failure-comment behavior.
- [ ] Step 5: Run tests and commit.

## Task 4: macro-agent Service Scaffold

Install macro-agent in `src/macro_agent_service/` with a thin TypeScript wrapper exposing REST endpoints.

**Files:**
- Create: `src/macro_agent_service/package.json`
- Create: `src/macro_agent_service/src/index.ts`
- Create: `src/macro_agent_service/teams/self-driving/team.yaml`
- Create: `src/macro_agent_service/Dockerfile`
- Modify: `src/governance_controller/docker-compose.yml`

- [ ] Step 1: `npm init` and `npm install macro-agent@latest` pinned exact.
- [ ] Step 2: Implement wrapper that boots `bootV2`, exposes REST on port 4000, forwards `workspaceManager.onEvent` to Controller `POST /events`.
- [ ] Step 3: Add minimal team YAML using OpenCode worker role.
- [ ] Step 4: Verify wrapper starts and health endpoint responds.
- [ ] Step 5: Commit service scaffold.

## Task 5: Real macro-agent Executor Client

Replace stub `MacroAgentClient` with real HTTP calls to macro-agent service.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/executor.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_macro_agent_client.py`

- [ ] Step 1: Add `macro_agent_base_url`, `macro_agent_api_token` to Settings.
- [ ] Step 2: Implement `start_run`, `status`, `cancel`, `collect` with httpx.
- [ ] Step 3: Update `MacroAgentExecutor` to use real client methods.
- [ ] Step 4: Mock tests for HTTP contract.
- [ ] Step 5: Contract test against running macro-agent service (optional, manual gate).
- [ ] Step 6: Run tests and commit.

## Task 6: opentasks Runtime DAG Materializer

Build runtime DAG from Plane dependencies and pass to macro-agent service.

**Files:**
- Create: `src/governance_controller/governance_controller/services/opentasks_materializer.py`
- Create: `src/governance_controller/governance_controller/schemas/opentasks.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`
- Test: `src/governance_controller/tests/test_opentasks_materializer.py`

- [ ] Step 1: Implement DAG fetch from Plane dependencies.
- [ ] Step 2: Define `OpentasksDAG` Pydantic schema with metadata.
- [ ] Step 3: Wire materialization into `EXEC_APPROVED -> READY` transition.
- [ ] Step 4: Tests for cycle detection and missing dependencies.
- [ ] Step 5: Run tests and commit.

## Task 7: Reconciliation Job

Periodic comparison of Plane state vs Controller state with CLI command.

**Files:**
- Create: `src/governance_controller/governance_controller/services/reconciliation_service.py`
- Modify: `src/governance_controller/governance_controller/cli.py`
- Test: `src/governance_controller/tests/test_reconciliation_service.py`

- [ ] Step 1: Implement divergence check.
- [ ] Step 2: Implement reconcile projection fields only; alert on content drift for EXEC_APPROVED+.
- [ ] Step 3: Add `reconcile` CLI command.
- [ ] Step 4: Tests.
- [ ] Step 5: Run tests and commit.

## Task 8: Intake Adapter (Telegram + Email)

Multi-channel intake normalizing to Plane drafts.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/telegram.py`
- Create: `src/governance_controller/governance_controller/adapters/email.py`
- Create: `src/governance_controller/governance_controller/services/idea_ingestion_service.py`
- Create: `src/governance_controller/governance_controller/api/intake.py`
- Test: `src/governance_controller/tests/test_intake.py`

- [ ] Step 1: Implement `RawIdea` schema and simple classifier.
- [ ] Step 2: Implement Telegram webhook handler with secret-token check.
- [ ] Step 3: Implement email webhook handler.
- [ ] Step 4: Wire `/intake/telegram` and `/intake/email` routes.
- [ ] Step 5: Tests for classification and Plane draft creation.
- [ ] Step 6: Run tests and commit.

## Task 9: Verification Failure Feedback + Terminal Alerting

Send failure feedback to macro-agent and real outbound alerts for terminal FAILED.

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py`
- Create: `src/governance_controller/governance_controller/services/alert_service.py`
- Test: `src/governance_controller/tests/test_verification_feedback.py`, `tests/test_alert_service.py`

- [ ] Step 1: Add `send_feedback` to `MacroAgentClient`.
- [ ] Step 2: Call feedback before retry in `VerificationService`.
- [ ] Step 3: Implement `AlertService` with Plane comment alert.
- [ ] Step 4: Replace audit-only alert with real alert.
- [ ] Step 5: Tests.
- [ ] Step 6: Run tests and commit.

## Task 10: Optional OPA Backend Client

Optional policy engine backend via OPA.

**Files:**
- Create: `src/governance_controller/governance_controller/services/opa_client.py`
- Modify: `src/governance_controller/governance_controller/services/policy_engine.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_opa_client.py`

- [ ] Step 1: Implement `OPAClient.evaluate`.
- [ ] Step 2: Add backend switch in `PolicyEngine`.
- [ ] Step 3: Tests.
- [ ] Step 4: Run tests and commit.

## Final Verification

- [ ] Full suite SQLite: `uv run pytest tests/ -q`
- [ ] Full suite PostgreSQL: `GC_TEST_DATABASE_URL=... uv run pytest tests/ -q`
- [ ] `uv run ruff check . && uv run mypy governance_controller`
- [ ] Update `docs/NEXT_STEPS.md` and `specs/SPEC-10-phase-plan.md` Phase 2 status.
