# Task 11 Report: Trigger Execution After Approval

## Status
Completed

## Changes
- `src/governance_controller/governance_controller/services/approval_service.py`
  - Added optional `executor: MacroAgentExecutor | None` constructor injection.
  - After an `EXECUTION` approval reaches `EXEC_APPROVED`, the service now transitions the task to `READY`, calls `executor.start(task_contract)`, creates an `Execution` record with `RUNNING` state, transitions the task to `RUNNING`, and logs `execution_start` audit event.
  - On macro-agent start failure, transitions task to `FAILED` and logs `execution_start_failed`.
- `src/governance_controller/governance_controller/services/state_machine.py`
  - Added `READY -> FAILED` transition to support the execution-start failure path.
- `src/governance_controller/governance_controller/models/execution.py`
  - Removed duplicate explicit `Index("ix_execution_task_id", "task_id")`; `Field(index=True)` already creates the index.
- `src/governance_controller/tests/test_approval_service.py`
  - Added `fake_executor` fixture and injected it into `ApprovalService` to avoid real network calls.
  - Updated the execution-approval test to assert `RUNNING` and verify executor invocation.
- `src/governance_controller/tests/test_execution_trigger.py`
  - New test module covering:
    - EXECUTION approval starts macro-agent and creates `Execution`.
    - Task ends in `RUNNING`.
    - Macro-agent start failure moves task to `FAILED` and logs `execution_start_failed`.
    - PLAN approval does not create an `Execution`.

## Verification
```bash
uv run pytest tests/test_execution_trigger.py tests/test_approval_service.py -v
# 9 passed, 1 warning

uv run ruff check governance_controller tests
# All checks passed
```

## Commit
- `da81276` feat: trigger macro-agent execution after EXEC_APPROVED

## Concerns
- None; the implementation follows exact values from the brief.
