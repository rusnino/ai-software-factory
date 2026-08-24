# Phase 2 Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the Governance Controller to real external systems: Plane CE for human-facing task projection, macro-agent for execution, and an Intake Adapter for multi-channel idea capture.

**Architecture:** Extend the existing FastAPI Governance Controller with HTTP-based adapters. Plane Adapter becomes a real REST client that projects Controller state to Plane and receives webhooks. macro-agent integration replaces the HTTP stub with real `/runs` endpoints, adds status/cancel/collect polling, and keeps the in-process Event Bridge for tests while adding an HTTP webhook receiver. Intake Adapter normalizes input from Telegram/Email/API into Plane drafts via the Plane Adapter. Each subsystem is built behind the existing abstraction interfaces so stubs can remain for isolated testing.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy/asyncpg, uv, httpx, pytest, structlog. Plane CE REST API (v3). macro-agent REST/ACP (alexngai/macro-agent). Optional: docker compose for Plane CE local dev.

**Spec:** `specs/SPEC-02-architecture.md`, `specs/SPEC-04-plane-integration.md`, `specs/SPEC-05-macro-agent-integration.md`, `specs/SPEC-07-intake.md`, `specs/SPEC-10-phase-plan.md`.

## Global Constraints

- Python projects use `uv` / `uvx`.
- No new external dependencies without documenting why.
- No secrets in markdown/yaml/config files committed to git.
- Governance Controller remains authoritative for approvals, state machine, policy, audit.
- Plane CE is a replaceable projection; do not rely on Plane to enforce transitions.
- macro-agent is execution orchestration only; it does not decide whether work may run.
- All approval paths converge to `POST /approvals`.
- Every significant code change must be accompanied by tests.
- Failed verification must never produce `DONE`.

---

## Task 1: Real Plane Adapter HTTP Client

**Files:**
- Create: `src/governance_controller/governance_controller/adapters/plane_client.py`
- Modify: `src/governance_controller/governance_controller/adapters/plane_adapter.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_plane_client.py`

**Interfaces:**
- Consumes: `Settings` (new Plane URL/token config), `TaskResponse` schema.
- Produces: `PlaneClient` with async methods: `get_task`, `update_task_state`, `add_comment`, `create_task`, `get_task_dependencies`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from governance_controller.adapters.plane_client import PlaneClient

async def test_plane_client_uses_config_base_url() -> None:
    client = PlaneClient(base_url="https://plane.example.com", token="tok", workspace_slug="ws", project_id="proj")
    assert client.base_url == "https://plane.example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plane_client.py::test_plane_client_uses_config_base_url -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'governance_controller.adapters.plane_client'"

- [ ] **Step 3: Write minimal implementation**

```python
from governance_controller.config import settings

class PlaneClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, workspace_slug: str | None = None, project_id: str | None = None):
        self.base_url = base_url or settings.plane_base_url
        self.token = token or settings.plane_api_token
        self.workspace_slug = workspace_slug or settings.plane_workspace_slug
        self.project_id = project_id or settings.plane_project_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plane_client.py::test_plane_client_uses_config_base_url -v`
Expected: PASS

- [ ] **Step 5: Add Settings fields**

Modify `src/governance_controller/governance_controller/config.py` to add optional `plane_base_url`, `plane_api_token`, `plane_workspace_slug`, `plane_project_id`.

- [ ] **Step 6: Implement CRUD methods with httpx**

Add `get_task`, `update_task_state`, `add_comment`, `create_task`, `get_task_dependencies` using `httpx.AsyncClient` with auth header and timeouts.

- [ ] **Step 7: Add httpx mocking tests**

Use `pytest-httpx` (already in test deps via `httpx` extra) to assert request shape and response parsing.

- [ ] **Step 8: Run full Plane Client test suite**

Run: `uv run pytest tests/test_plane_client.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/governance_controller/governance_controller/adapters/plane_client.py src/governance_controller/governance_controller/config.py src/governance_controller/tests/test_plane_client.py
git commit -m "feat(plane): add real Plane CE HTTP client with config wiring"
```

---

## Task 2: Plane Webhook Receiver

**Files:**
- Create: `src/governance_controller/governance_controller/api/webhooks.py`
- Modify: `src/governance_controller/governance_controller/main.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_plane_webhook.py`

**Interfaces:**
- Consumes: `PlaneClient` (for status reverts/comments), `ApprovalService`, `TaskService`.
- Produces: `POST /webhooks/plane` endpoint validating and translating Plane events per SPEC-04 §4.3a.

- [ ] **Step 1: Write the failing test**

```python
async def test_plane_webhook_rejects_bulk_operation(client: AsyncClient) -> None:
    payload = {"source": "plane", "event_type": "state.changed", "task_id": "TASK-1", "project_id": "PROJECT-1", "payload": {"previous": {"state": "Proposed"}, "current": {"state": "Plan Approved"}, "actor": "human@example.com", "actor_type": "human", "operation": "bulk_update"}}
    response = await client.post("/webhooks/plane", json=payload)
    assert response.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plane_webhook.py::test_plane_webhook_rejects_bulk_operation -v`
Expected: FAIL with "404 Not Found"

- [ ] **Step 3: Scaffold webhook route and Pydantic schema**

Create `PlaneWebhookEvent` schema and `POST /webhooks/plane` route that returns 422 for `operation == "bulk_update"`.

- [ ] **Step 4: Wire route into main app**

Modify `main.py` to include the webhook router under `/webhooks`.

- [ ] **Step 5: Implement SPEC-04 §4.3a translation**

Add logic:
1. `actor_type` must be `human`.
2. Transition maps to approval type.
3. Previous Plane state matches current Controller state.
4. Not bulk/migration/automation.
5. Call `ApprovalService.approve()`.

- [ ] **Step 6: Add tests for valid plan/execution/merge transitions and rejections**

- [ ] **Step 7: Run full webhook test suite**

Run: `uv run pytest tests/test_plane_webhook.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/governance_controller/governance_controller/api/webhooks.py src/governance_controller/governance_controller/main.py src/governance_controller/tests/test_plane_webhook.py
git commit -m "feat(plane): add Plane webhook receiver with approval translation"
```

---

## Task 3: Controller → Plane Projection Service

**Files:**
- Create: `src/governance_controller/governance_controller/services/plane_projection_service.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py` (optional hook points)
- Modify: `src/governance_controller/governance_controller/services/task_service.py` (optional hook points)
- Test: `src/governance_controller/tests/test_plane_projection_service.py`

**Interfaces:**
- Consumes: `PlaneClient`, `Task`, `TaskState`.
- Produces: `PlaneProjectionService.sync_task_state(task_id)` that updates Plane execution-status fields and adds comments on terminal failures.

- [ ] **Step 1: Write the failing test**

```python
async def test_projection_service_updates_plane_state(httpx_mock, db_session: AsyncSession) -> None:
    httpx_mock.add_response(status_code=200, json={"id": "TASK-1", "state": "In Progress"})
    service = PlaneProjectionService(db_session)
    await service.sync_task_state("TASK-1", TaskState.RUNNING)
    request = httpx_mock.get_request()
    assert request.url.path.endswith("/api/v1/workspaces/ws/projects/proj/issues/TASK-1/")
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with "PlaneProjectionService not defined"

- [ ] **Step 3: Implement PlaneProjectionService**

Map `TaskState` to Plane state per SPEC-04 §4.5. Use `PlaneClient.update_task_state`. On `FAILED`, also call `PlaneClient.add_comment` with failure reason from latest audit log.

- [ ] **Step 4: Add state mapping tests for all task states**

- [ ] **Step 5: Wire projection into state transitions**

Call `PlaneProjectionService.sync_task_state` after successful state transitions in `ApprovalService` and `EventBridge.handle()` when Plane integration is enabled. Keep behind config flag so tests without Plane still pass.

- [ ] **Step 6: Run full projection test suite**

Run: `uv run pytest tests/test_plane_projection_service.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/governance_controller/governance_controller/services/plane_projection_service.py src/governance_controller/tests/test_plane_projection_service.py
git commit -m "feat(plane): add projection service syncing Controller state to Plane"
```

---

## Task 4: Real macro-agent `/runs` Client

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/executor.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_macro_agent_client.py`

**Interfaces:**
- Consumes: `TaskContract`, `Execution` model, `Settings`.
- Produces: `MacroAgentClient.start_run`, `status`, `cancel`, `collect` with real HTTP paths.

- [ ] **Step 1: Write the failing test**

```python
async def test_macro_agent_client_starts_run(httpx_mock) -> None:
    httpx_mock.add_response(status_code=201, json={"run_id": "run-123"})
    client = MacroAgentClient(base_url="https://macro.example.com", token="tok")
    ref = await client.start_run(contract=TaskContract(task_id="t", project_id="p", proposed_by="a", objective="o", acceptance=["ok"]))
    assert ref.run_id == "run-123"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing method or wrong return type.

- [ ] **Step 3: Implement real HTTP methods**

Replace stub with `httpx.AsyncClient` calls to `/runs`, `/runs/{id}/status`, `/runs/{id}/cancel`, `/runs/{id}/collect`. Parse responses into Pydantic `ExecutionRef` / `ExecutionStatus` / `ExecutionResult`.

- [ ] **Step 4: Update executor to use status/cancel/collect**

Modify `MacroAgentExecutor` to call `client.start_run`, `status`, `cancel`, and `collect`.

- [ ] **Step 5: Add config fields**

Add `macro_agent_base_url`, `macro_agent_api_token` to `Settings`.

- [ ] **Step 6: Run macro-agent client test suite**

Run: `uv run pytest tests/test_macro_agent_client.py tests/test_macro_agent_executor.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/governance_controller/governance_controller/adapters/macro_agent/client.py src/governance_controller/governance_controller/adapters/macro_agent/executor.py src/governance_controller/governance_controller/config.py src/governance_controller/tests/test_macro_agent_client.py
git commit -m "feat(macro-agent): replace stub client with real /runs HTTP client"
```

---

## Task 5: macro-agent Event Bridge HTTP Receiver + Polling Fallback

**Files:**
- Create: `src/governance_controller/governance_controller/api/event_bridge.py` (or extend `api/events.py`)
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/event_bridge.py`
- Test: `src/governance_controller/tests/test_event_bridge_http.py`

**Interfaces:**
- Consumes: macro-agent workspace events via HTTP `POST /events` (already exists) or dedicated bridge endpoint.
- Produces: Idempotent event handling with retry-aware dedup; fallback polling when bridge is unhealthy.

- [ ] **Step 1: Write the failing test**

```python
async def test_event_bridge_http_endpoint_is_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    # First delivery transitions task; second delivery is ignored.
    event = {"type": "landing:completed", "metadata": {"controller_task_id": "task-1", "event_id": "evt-1", "event_timestamp": "2026-08-24T00:00:00Z"}, "payload": {}}
    response1 = await client.post("/events", json=event)
    response2 = await client.post("/events", json=event)
    assert response1.status_code == 204
    assert response2.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Expected: depends on current behavior; verify and adjust.

- [ ] **Step 3: Ensure existing `/events` endpoint handles bridge events**

The endpoint exists in `api/events.py`; confirm it routes macro-agent workspace events through `EventBridge.handle()` and returns 204.

- [ ] **Step 4: Add polling fallback service**

Create `MacroAgentPoller` that polls `/runs/{id}/status` every 30 seconds if no event received within timeout; marks `BLOCKED` after 2x task timeout. Integrate into a lightweight background task or explicit endpoint for Phase 2 (do not add Celery/Temporal yet).

- [ ] **Step 5: Add polling fallback tests with mocked time**

- [ ] **Step 6: Run full Event Bridge test suite**

Run: `uv run pytest tests/test_event_bridge.py tests/test_event_bridge_http.py tests/test_events_endpoint.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/governance_controller/governance_controller/adapters/macro_agent/event_bridge.py src/governance_controller/governance_controller/api/events.py src/governance_controller/governance_controller/services/macro_agent_poller.py src/governance_controller/tests/test_event_bridge_http.py
git commit -m "feat(macro-agent): add HTTP Event Bridge receiver and polling fallback"
```

---

## Task 6: opentasks Runtime DAG Materializer

**Files:**
- Create: `src/governance_controller/governance_controller/services/opentasks_materializer.py`
- Create: `src/governance_controller/governance_controller/schemas/opentasks.py`
- Modify: `src/governance_controller/governance_controller/services/approval_service.py`
- Test: `src/governance_controller/tests/test_opentasks_materializer.py`

**Interfaces:**
- Consumes: `PlaneClient.get_task_dependencies`, approved `Task` rows.
- Produces: JSON payload for opentasks runtime DAG with `metadata.plane_task_id` and dependencies.

- [ ] **Step 1: Write the failing test**

```python
async def test_materializer_builds_subgraph(httpx_mock, db_session: AsyncSession) -> None:
    httpx_mock.add_response(status_code=200, json={"id": "TASK-1", "name": "root"})
    httpx_mock.add_response(status_code=200, json={"results": [{"id": "TASK-2", "name": "dep", "source": "TASK-1"}]})
    materializer = OpentasksMaterializer(db_session)
    dag = await materializer.materialize("TASK-1")
    assert dag.root_id == "TASK-1"
    assert any(node.id == "TASK-2" for node in dag.nodes)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing class.

- [ ] **Step 3: Implement OpentasksMaterializer**

Fetch task and dependencies from Plane. Build DAG. Return Pydantic `OpentasksDAG`.

- [ ] **Step 4: Wire into EXECUTION approval flow**

After `EXEC_APPROVED -> READY`, call materializer and persist DAG JSON on `Execution` row or pass to macro-agent in `MacroAgentExecutor.start()`.

- [ ] **Step 5: Add tests for cycle detection and missing dependencies**

- [ ] **Step 6: Run materializer test suite**

Run: `uv run pytest tests/test_opentasks_materializer.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/governance_controller/governance_controller/services/opentasks_materializer.py src/governance_controller/governance_controller/schemas/opentasks.py src/governance_controller/tests/test_opentasks_materializer.py
git commit -m "feat(opentasks): add runtime DAG materializer from Plane dependencies"
```

---

## Task 7: Reconciliation Job

**Files:**
- Create: `src/governance_controller/governance_controller/services/reconciliation_service.py`
- Modify: `src/governance_controller/governance_controller/cli.py` (add CLI command)
- Test: `src/governance_controller/tests/test_reconciliation_service.py`

**Interfaces:**
- Consumes: `PlaneClient`, `TaskService`, `PlaneProjectionService`.
- Produces: Periodic comparison of Plane state vs Controller state; alerts on divergence; corrects only projection fields.

- [ ] **Step 1: Write the failing test**

```python
async def test_reconciliation_detects_plane_state_drift(httpx_mock, db_session: AsyncSession) -> None:
    httpx_mock.add_response(status_code=200, json={"id": "TASK-1", "state": "Done"})
    service = ReconciliationService(db_session)
    divergences = await service.check_divergence(["TASK-1"])
    assert len(divergences) == 1
    assert divergences[0].controller_state == "HUMAN_REVIEW"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing class.

- [ ] **Step 3: Implement ReconciliationService.check_divergence**

Compare Controller `Task.state` with Plane state via `PlaneClient.get_task`. Return list of divergences.

- [ ] **Step 4: Implement reconcile**

For projection fields (execution status), push Controller state to Plane. For content fields when task >= EXEC_APPROVED, alert human.

- [ ] **Step 5: Add CLI command `reconcile`**

Add to `cli.py`: `uv run python -m governance_controller.cli reconcile --project-id PROJECT-1`.

- [ ] **Step 6: Run reconciliation test suite**

Run: `uv run pytest tests/test_reconciliation_service.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/governance_controller/governance_controller/services/reconciliation_service.py src/governance_controller/governance_controller/cli.py src/governance_controller/tests/test_reconciliation_service.py
git commit -m "feat(reconciliation): add Plane vs Controller divergence check and CLI"
```

---

## Task 8: Intake Adapter — Telegram Bot Handler

**Files:**
- Modify: `src/governance_controller/governance_controller/adapters/telegram.py`
- Create: `src/governance_controller/governance_controller/services/idea_ingestion_service.py`
- Create: `src/governance_controller/governance_controller/api/intake.py`
- Test: `src/governance_controller/tests/test_intake_telegram.py`

**Interfaces:**
- Consumes: Telegram webhook payload.
- Produces: Normalized `RawIdea` → classified → Plane draft via `PlaneClient.create_task`.

- [ ] **Step 1: Write the failing test**

```python
async def test_telegram_intake_creates_raw_idea(client: AsyncClient, httpx_mock) -> None:
    httpx_mock.add_response(status_code=201, json={"id": "TASK-NEW"})
    payload = {"message": {"chat": {"id": 1}, "from": {"username": "user1"}, "text": "New feature idea"}}
    response = await client.post("/intake/telegram", json=payload)
    assert response.status_code == 202
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with 404.

- [ ] **Step 3: Implement RawIdea schema and IdeaIngestionService**

Create `RawIdea` schema per SPEC-07 §7.3. Implement simple classifier: keyword-based for Phase 2 (no LLM yet), returns `needs-triage` or `create_draft`.

- [ ] **Step 4: Implement Telegram webhook handler**

Validate Telegram secret token (constant-time). Normalize payload to `RawIdea`. Call `IdeaIngestionService.ingest`. Return 202.

- [ ] **Step 5: Wire `/intake/telegram` route**

Add router in `api/intake.py` and include in `main.py`.

- [ ] **Step 6: Add tests for classification and spam handling**

- [ ] **Step 7: Run intake test suite**

Run: `uv run pytest tests/test_intake_telegram.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/governance_controller/governance_controller/adapters/telegram.py src/governance_controller/governance_controller/services/idea_ingestion_service.py src/governance_controller/governance_controller/api/intake.py src/governance_controller/tests/test_intake_telegram.py
git commit -m "feat(intake): add Telegram intake adapter and idea ingestion service"
```

---

## Task 9: Email Intake Adapter (Optional but SPEC-07 P2)

**Files:**
- Create: `src/governance_controller/governance_controller/adapters/email.py`
- Modify: `src/governance_controller/governance_controller/api/intake.py`
- Test: `src/governance_controller/tests/test_intake_email.py`

**Interfaces:**
- Consumes: JSON webhook simulating email forward.
- Produces: `RawIdea` → `IdeaIngestionService`.

- [ ] **Step 1: Write the failing test**

```python
async def test_email_intake_creates_raw_idea(client: AsyncClient, httpx_mock) -> None:
    httpx_mock.add_response(status_code=201, json={"id": "TASK-EMAIL"})
    payload = {"from": "user@example.com", "subject": "Bug report", "body_text": "Login broken"}
    response = await client.post("/intake/email", json=payload)
    assert response.status_code == 202
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with 404.

- [ ] **Step 3: Implement email adapter**

Normalize email fields to `RawIdea`. Reuse `IdeaIngestionService`.

- [ ] **Step 4: Wire `/intake/email` route**

- [ ] **Step 5: Run email intake tests**

Run: `uv run pytest tests/test_intake_email.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/governance_controller/governance_controller/adapters/email.py src/governance_controller/governance_controller/api/intake.py src/governance_controller/tests/test_intake_email.py
git commit -m "feat(intake): add email intake adapter"
```

---

## Task 10: Verification Failure Feedback to macro-agent

**Files:**
- Modify: `src/governance_controller/governance_controller/services/verification_service.py`
- Modify: `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- Test: `src/governance_controller/tests/test_verification_feedback.py`

**Interfaces:**
- Consumes: verification report, `Execution`.
- Produces: Feedback message sent to macro-agent via HTTP `POST /runs/{id}/feedback` (or macro-agent's defined endpoint).

- [ ] **Step 1: Write the failing test**

```python
async def test_verification_failure_sends_feedback(httpx_mock, db_session: AsyncSession) -> None:
    httpx_mock.add_response(status_code=200, json={})
    service = VerificationService()
    await service._send_failure_feedback(db_session, Execution(id="exec-1", task_id="t", macro_agent_run_id="run-1"), {"passed": False, "checks": []})
    request = httpx_mock.get_request()
    assert request.url.path.endswith("/runs/run-1/feedback")
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing method.

- [ ] **Step 3: Add `send_feedback` to MacroAgentClient**

Implement `MacroAgentClient.send_feedback(run_id, payload)`.

- [ ] **Step 4: Call feedback from retry path**

In `VerificationService._start_retry_execution()`, before re-invoking executor, send failure feedback with summary of last verification report.

- [ ] **Step 5: Run verification feedback tests**

Run: `uv run pytest tests/test_verification_feedback.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/governance_controller/governance_controller/services/verification_service.py src/governance_controller/governance_controller/adapters/macro_agent/client.py src/governance_controller/tests/test_verification_feedback.py
git commit -m "feat(verification): send failure feedback to macro-agent before retry"
```

---

## Task 11: Outbound Alert Channel for Terminal FAILED

**Files:**
- Create: `src/governance_controller/governance_controller/services/alert_service.py`
- Modify: `src/governance_controller/governance_controller/services/verification_service.py`
- Test: `src/governance_controller/tests/test_alert_service.py`

**Interfaces:**
- Consumes: terminal failure event.
- Produces: Webhook/Email/Plane comment alert configurable via `Settings.alert_*`.

- [ ] **Step 1: Write the failing test**

```python
async def test_alert_service_sends_plane_comment_on_terminal_failure(httpx_mock, db_session: AsyncSession) -> None:
    httpx_mock.add_response(status_code=201, json={"id": "COMMENT-1"})
    service = AlertService(db_session)
    await service.alert_terminal_failure(task_id="TASK-1", reason="max_retries_exhausted")
    request = httpx_mock.get_request()
    assert "/comments" in request.url.path
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing class.

- [ ] **Step 3: Implement AlertService**

For Phase 2 implement Plane comment alert first (already have `PlaneClient.add_comment`). Add webhook/email stubs behind config.

- [ ] **Step 4: Call from terminal failure path**

Replace audit-only `_alert_human_terminal_failure` with `AlertService.alert_terminal_failure`.

- [ ] **Step 5: Run alert tests**

Run: `uv run pytest tests/test_alert_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/governance_controller/governance_controller/services/alert_service.py src/governance_controller/governance_controller/services/verification_service.py src/governance_controller/tests/test_alert_service.py
git commit -m "feat(alerts): add real outbound Plane comment alert for terminal failures"
```

---

## Task 12: Optional OPA Policy Engine Backend

**Files:**
- Create: `src/governance_controller/governance_controller/services/opa_client.py`
- Modify: `src/governance_controller/governance_controller/services/policy_engine.py`
- Modify: `src/governance_controller/governance_controller/config.py`
- Test: `src/governance_controller/tests/test_opa_client.py`

**Interfaces:**
- Consumes: `TaskContract`, `ProjectProfile`.
- Produces: `PolicyResult` from OPA `/v1/data/governance/allow`.

- [ ] **Step 1: Write the failing test**

```python
async def test_opa_client_evaluates_policy(httpx_mock) -> None:
    httpx_mock.add_response(status_code=200, json={"result": {"allow": True, "violations": []}})
    client = OPAClient(base_url="http://opa:8181")
    result = await client.evaluate(contract=TaskContract(...), profile=ProjectProfile(...))
    assert result.allowed is True
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with missing class.

- [ ] **Step 3: Implement OPAClient**

POST to `/v1/data/governance/allow` with input JSON. Parse response.

- [ ] **Step 4: Add PolicyEngine backend switch**

If `settings.opa_base_url` is set, use OPA; otherwise use embedded engine. Keep default embedded for Phase 2 until OPA policies are deployed.

- [ ] **Step 5: Run OPA tests**

Run: `uv run pytest tests/test_opa_client.py tests/test_policy_engine.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/governance_controller/governance_controller/services/opa_client.py src/governance_controller/governance_controller/services/policy_engine.py src/governance_controller/tests/test_opa_client.py
git commit -m "feat(policy): add optional OPA backend client"
```

---

## Final Verification

- [ ] Run full test suite on SQLite:
  `uv run pytest tests/ -q`
  Expected: PASS
- [ ] Run full test suite on PostgreSQL:
  `GC_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/gc_test uv run pytest tests/ -q`
  Expected: PASS
- [ ] Run linters:
  `uv run ruff check . && uv run mypy governance_controller`
  Expected: clean
- [ ] Update `docs/NEXT_STEPS.md` to reflect completed Phase 2 tasks.
- [ ] Update `specs/SPEC-10-phase-plan.md` Phase 2 checklist if applicable.
- [ ] Update `AGENTS.md` if any new review/gap tracking conventions changed.
