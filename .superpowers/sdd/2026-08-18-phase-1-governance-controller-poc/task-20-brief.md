# Task 20 Brief: Final Integration / End-to-End Smoke Test

**Goal:** Write an end-to-end smoke test that exercises the full Phase 1 path: create task → plan approval → execution approval → macro-agent execution → event bridge state updates → merge approval → DONE. Use in-memory stub adapters.

**Files to create:**
- `src/governance_controller/tests/test_phase1_smoke.py`

**Exact values to use verbatim:**

Scenario:
1. POST `/tasks` with a `TaskContract` and `ProjectProfile`.
2. POST `/approvals` with `PLAN` approval.
3. POST `/approvals` with `EXECUTION` approval (mocked executor returns `{"run_id": "smoke-run-1"}`).
4. Assert task state is `RUNNING` and execution record exists.
5. Simulate macro-agent events via `EventBridge.handle`:
   - `landing:completed` → `AGENT_REVIEW`
   - `landing:completed` → remains `AGENT_REVIEW` (idempotent)
   - `conflict:created` → `BLOCKED`
   - `conflict:resolved` → `RUNNING`
   - `landing:completed` → `AGENT_REVIEW`
   - Direct state transition to `HUMAN_REVIEW`.
6. POST `/approvals` with `MERGE` approval.
7. Assert task state is `DONE`.

**Interfaces produced:**
- `tests/test_phase1_smoke.py`

**Interfaces consumed:**
- All existing: tasks API, approvals API, EventBridge, ApprovalService, Execution model.

**Constraints:**
- Must use FastAPI TestClient/AsyncClient with DB override.
- Must mock `MacroAgentExecutor.start`.
- Must not make real external calls.
- Must include assertions on audit log entries for key transitions.

**Verification steps:**
1. Run `uv run pytest tests/test_phase1_smoke.py -v`.
2. Run `uv run pytest tests/ -q`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `test: add Phase 1 end-to-end smoke test`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-20-report.md`
