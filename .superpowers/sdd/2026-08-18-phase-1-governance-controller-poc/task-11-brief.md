# Task 11 Brief: Trigger Execution After Approval

**Goal:** Wire the macro-agent executor into the ApprovalService so that an `execution` approval transitions the task to `READY`, starts the macro-agent run, creates an `Execution` record, and transitions to `RUNNING`.

**Files to modify:**
- `src/governance_controller/governance_controller/services/approval_service.py`
- `src/governance_controller/governance_controller/api/approvals.py` (if needed to inject executor)
- `src/governance_controller/tests/test_execution_trigger.py`

**Exact values to use verbatim:**

When `approval_type == ApprovalType.EXECUTION`:
1. Validate policy.
2. Transition task from current state (must be `EXEC_APPROVED`) to `READY`.
3. Call `MacroAgentExecutor.start(task_contract)`.
4. Create `Execution` record:
   - `id`: `str(uuid4())`
   - `task_id`: task.id
   - `macro_agent_run_id`: result["run_id"]
   - `state`: `TaskState.RUNNING`
   - `started_at`: utcnow
5. Transition task from `READY` to `RUNNING`.
6. Log audit event:
   - `event_type="execution_start"`
   - payload with `execution_id` and `macro_agent_run_id`
7. Save approval, execution, and task.
8. Return task.

When macro-agent start fails:
1. Transition task to `FAILED`.
2. Log audit event `execution_start_failed` with error details.
3. Raise `RuntimeError` or return task in FAILED state.

**Interfaces produced:**
- Updated `ApprovalService.approve(...)` that optionally starts execution.

**Interfaces consumed:**
- `MacroAgentExecutor` from `governance_controller.adapters.macro_agent.executor`
- `Execution` from `governance_controller.models.execution`
- `StateMachine` from `governance_controller.services.state_machine`
- `AuditService` from `governance_controller.services.audit_service`
- `TaskState`, `ApprovalType` from `governance_controller.constants`

**Constraints:**
- ApprovalService must accept executor injection for testing.
- If `approval_type` is not `EXECUTION`, behavior from Task 5 remains unchanged, but still log `state_change` audit event.
- `Execution` record creation only for `EXECUTION` approvals.

**Verification steps:**
1. Write `tests/test_execution_trigger.py` covering:
   - execution approval starts macro-agent and creates Execution.
   - created task state becomes RUNNING.
   - macro-agent start failure transitions task to FAILED and logs audit event.
   - plan approval does not create Execution.
2. Run `uv run pytest tests/test_execution_trigger.py tests/test_approval_service.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: trigger macro-agent execution after EXEC_APPROVED`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-11-report.md`
