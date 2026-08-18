# Task 7 Brief: REST API Endpoints

**Goal:** Expose FastAPI endpoints for task creation, task lookup, approval submission, and execution lookup.

**Files to create:**
- `src/governance_controller/governance_controller/main.py`
- `src/governance_controller/governance_controller/api/tasks.py`
- `src/governance_controller/governance_controller/api/approvals.py`
- `src/governance_controller/governance_controller/api/executions.py`
- `src/governance_controller/governance_controller/schemas/approval.py`
- `src/governance_controller/governance_controller/schemas/task.py`
- `src/governance_controller/tests/test_task_api.py`
- `src/governance_controller/tests/test_approval_endpoint.py`

**Files to modify:**
- `src/governance_controller/governance_controller/api/__init__.py` (re-export routers if desired)

**Exact values to use verbatim:**

Endpoints:
- `POST /tasks` — create task from TaskContract payload
- `GET /tasks/{id}` — return task by id
- `POST /approvals` — single authoritative approval endpoint
- `GET /executions/{id}` — return execution by id

`ApprovalRequest` schema fields:
- `task_id: str`
- `approval_type: ApprovalType`
- `source: str`
- `actor: str`
- `timestamp: str` (ISO 8601 string)
- `comment: str | None = None`

`ApprovalResponse` schema fields:
- `task_id: str`
- `state: str`
- `approved: bool`

`TaskCreateRequest` schema fields:
- `task_contract: TaskContract`
- `project_profile: ProjectProfile`

`TaskResponse` schema fields:
- `id: str`
- `state: str`
- `project_id: str`
- `created_at: datetime`
- `updated_at: datetime | None`

`ExecutionResponse` schema fields:
- `id: str`
- `task_id: str`
- `macro_agent_run_id: str | None`
- `state: str`
- `started_at: datetime`
- `ended_at: datetime | None`

Approval endpoint behavior:
1. Look up task by `task_id`.
2. Load project_profile and task_contract from task record (stored at creation).
3. Generate idempotency key from `(task_id, approval_type, actor, timestamp)` rounded to seconds.
4. Call `ApprovalService.approve(...)`.
5. Return `ApprovalResponse`.
6. If policy violation: return HTTP 422 with violation list.
7. If invalid transition: return HTTP 409.
8. If task not found: return HTTP 404.

Task creation endpoint behavior:
1. Validate TaskContract and ProjectProfile schemas.
2. Create `Task` record with `id=task_contract.task_id`, `project_id=task_contract.project_id`, `state=PROPOSED`, storing `task_contract_json`.
3. Also create/upsert `ProjectProfile` record? For Phase 1, store project profile in a separate table or just keep it in memory. Simpler: create a `project_profiles` table and upsert on creation.
4. Return `TaskResponse`.

**Interfaces produced:**
- `app` from `governance_controller.main`
- `tasks.router`, `approvals.router`, `executions.router`
- `ApprovalRequest`, `ApprovalResponse`, `TaskCreateRequest`, `TaskResponse`, `ExecutionResponse` schemas

**Interfaces consumed:**
- `TaskService.create(...) -> Task`
- `TaskService.get_by_id(...) -> Task | None`
- `ApprovalService.approve(...)`
- `Execution` model

**Constraints:**
- Use FastAPI dependency injection for `AsyncSession` (`get_db`).
- Add ` lifespan` context manager that calls `init_db()` on startup.
- Keep a single authoritative `POST /approvals` endpoint.

**Verification steps:**
1. Include `httpx` client pytest fixture in `tests/test_task_api.py` and `tests/test_approval_endpoint.py`.
2. Tests should use the SQLite-compatible test DB.
3. Cover:
   - Create task returns 201.
   - Get task returns 200.
   - Approval without previous plan approval returns 409.
   - Approval plan advances state.
   - Approval execution advances state.
   - Approval merge advances to DONE.
4. Run `uv run pytest tests/test_task_api.py tests/test_approval_endpoint.py -v`.
5. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add REST API endpoints for tasks, approvals, executions`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-7-report.md`
