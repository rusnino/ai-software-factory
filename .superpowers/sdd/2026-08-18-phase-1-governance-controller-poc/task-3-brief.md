# Task 3 Brief: Task and State Machine Models

**Goal:** Define the `Task` SQLModel entity and a deterministic state machine that governs all valid transitions in Phase 1.

**Files to create:**
- `src/governance_controller/governance_controller/constants.py`
- `src/governance_controller/governance_controller/models/task.py`
- `src/governance_controller/governance_controller/services/state_machine.py`
- `src/governance_controller/tests/test_state_machine.py`

**Files to modify:**
- `src/governance_controller/governance_controller/models/__init__.py` (re-export models)

**Exact values to use verbatim:**

States (`TaskState`):
```python
PROPOSED = "PROPOSED"
PLAN_APPROVED = "PLAN_APPROVED"
EXEC_APPROVED = "EXEC_APPROVED"
READY = "READY"
RUNNING = "RUNNING"
AGENT_REVIEW = "AGENT_REVIEW"
HUMAN_REVIEW = "HUMAN_REVIEW"
DONE = "DONE"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
```

Approval types (`ApprovalType`):
```python
PLAN = "plan"
EXECUTION = "execution"
MERGE = "merge"
```

State transition table:
- `PROPOSED` → `PLAN_APPROVED`
- `PLAN_APPROVED` → `EXEC_APPROVED`
- `EXEC_APPROVED` → `READY`
- `READY` → `RUNNING`
- `RUNNING` → `AGENT_REVIEW`, `BLOCKED`, `FAILED`
- `AGENT_REVIEW` → `HUMAN_REVIEW`, `FAILED`
- `HUMAN_REVIEW` → `DONE`, `FAILED`, `RUNNING`
- `BLOCKED` → `RUNNING`, `FAILED`
- `FAILED` → (no transitions)
- `DONE` → (no transitions)

Forbidden transitions without approval:
- `PROPOSED → EXEC_APPROVED`
- `PLAN_APPROVED → READY`
- `EXEC_APPROVED → RUNNING`
- `RUNNING → DONE`
- `HUMAN_REVIEW → DONE`

`Task` model fields:
- `id: str` (primary key)
- `state: TaskState` (default `PROPOSED`)
- `project_id: str`
- `task_contract_json: dict` (default `{}`, sa_type=dict)
- `created_at: datetime` (default utcnow)
- `updated_at: Optional[datetime]` (None)

**Interfaces produced:**
- `TaskState` enum from `governance_controller.constants`
- `ApprovalType` enum from `governance_controller.constants`
- `Task` SQLModel from `governance_controller.models.task`
- `StateMachine.transition(task, target_state) -> Task`

**Constraints:**
- Use SQLModel for the entity.
- State machine is pure Python, no DB access needed.
- Raise `ValueError` with clear message on invalid transition.
- Update `updated_at` on successful transition.

**Verification steps:**
1. Write `tests/test_state_machine.py` covering:
   - valid transitions (`PROPOSED → PLAN_APPROVED`, `HUMAN_REVIEW → DONE`)
   - invalid transitions (`PROPOSED → RUNNING`, `RUNNING → DONE`)
2. Run `uv run pytest tests/test_state_machine.py -v`.
3. Run `uv run ruff check .`.

**Commit message:** `feat: add Task model and StateMachine`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-3-report.md`
