# Task 10 Brief: macro-agent Executor Abstraction

**Goal:** Create a Python client and executor abstraction that the Governance Controller uses to start, query, and collect results from a macro-agent instance.

**Files to create:**
- `src/governance_controller/governance_controller/adapters/macro_agent/client.py`
- `src/governance_controller/governance_controller/adapters/macro_agent/executor.py`
- `src/governance_controller/governance_controller/models/execution.py`
- `src/governance_controller/tests/test_macro_agent_executor.py`

**Files to modify:**
- `src/governance_controller/governance_controller/models/__init__.py` (re-export Execution)
- `src/governance_controller/governance_controller/adapters/macro_agent/__init__.py` (re-export)

**Exact values to use verbatim:**

`Execution` model fields:
- `id: str` (primary key)
- `task_id: str` (index)
- `macro_agent_run_id: str | None = None`
- `state: TaskState`
- `started_at: datetime = utcnow`
- `ended_at: datetime | None = None`

`MacroAgentClient` methods:
- `async def start(self, payload: dict) -> dict`
- `async def status(self, run_id: str) -> dict`
- `async def cancel(self, run_id: str) -> dict`
- `async def collect(self, run_id: str) -> dict`

Base URL: from `Settings.macro_agent_base_url` (default `http://localhost:3000`).

HTTP endpoints used:
- `POST {base_url}/runs` → start
- `GET {base_url}/runs/{run_id}` → status
- `POST {base_url}/runs/{run_id}/cancel` → cancel
- `GET {base_url}/runs/{run_id}/collect` → collect

`MacroAgentExecutor` methods:
- `async def start(self, task_contract: TaskContract) -> dict`
- `async def status(self, run_id: str) -> dict`
- `async def cancel(self, run_id: str) -> dict`
- `async def collect(self, run_id: str) -> dict`

`MacroAgentExecutor.start` payload:
```python
{
    "task_id": task_contract.task_id,
    "team": task_contract.execution.team,
    "harness": task_contract.execution.harness,
    "objective": task_contract.objective,
    "acceptance": task_contract.acceptance,
}
```

**Interfaces produced:**
- `Execution` SQLModel from `governance_controller.models.execution`
- `MacroAgentClient` from `governance_controller.adapters.macro_agent.client`
- `MacroAgentExecutor` from `governance_controller.adapters.macro_agent.executor`

**Interfaces consumed:**
- `TaskContract` from `governance_controller.schemas.task_contract`
- `Settings` from `governance_controller.config`
- `TaskState` from `governance_controller.constants`

**Constraints:**
- Use `httpx.AsyncClient` for HTTP calls.
- Raise exceptions on HTTP errors (use `raise_for_status`).
- Executor must accept an optional `MacroAgentClient` for test injection.
- No opentasks/materialization logic in this task.

**Verification steps:**
1. Write `tests/test_macro_agent_executor.py` covering:
   - `start` sends correct payload and returns run_id.
   - `status` fetches status by run_id.
   - `cancel` sends POST cancel.
   - `collect` fetches collect.
   - HTTP error raises exception.
2. Use `unittest.mock.AsyncMock` or `pytest-httpx` for mocking.
3. Run `uv run pytest tests/test_macro_agent_executor.py -v`.
4. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add macro-agent executor abstraction and Execution model`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-10-report.md`
