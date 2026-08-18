# Task 20 Report: Final Integration / End-to-End Smoke Test

## Status

COMPLETED

## Commits

- `4902944` - `test: add Phase 1 end-to-end smoke test`

## Files Changed

- `src/governance_controller/tests/test_phase1_smoke.py` (created)
- `src/governance_controller/governance_controller/services/state_machine.py` (modified)
- `src/governance_controller/governance_controller/services/approval_service.py` (modified)

## Implementation Notes

Created `tests/test_phase1_smoke.py` covering the full Phase 1 path:

1. POST `/tasks` creates a `PROPOSED` task.
2. POST `/approvals` with `PLAN` approval advances to `PLAN_APPROVED`.
3. POST `/approvals` with `EXECUTION` approval triggers a mocked `MacroAgentExecutor.start` returning `{"run_id": "smoke-run-1"}` and advances through `READY` to `RUNNING`.
4. Asserted task state is `RUNNING` and an `Execution` record exists with the expected `macro_agent_run_id`.
5. Simulated macro-agent events via `EventBridge.handle`:
   - `landing:completed` → `AGENT_REVIEW`
   - Duplicate `landing:completed` → idempotent, remains `AGENT_REVIEW`
   - `conflict:created` → `BLOCKED`
   - `conflict:resolved` → `RUNNING`
   - `landing:completed` → `AGENT_REVIEW`
   - Direct `StateMachine.transition` to `HUMAN_REVIEW`
6. POST `/approvals` with `MERGE` approval advances to `DONE`.
7. Asserted final task state is `DONE`.

Audit log assertions verify entries for approvals, state changes, `execution_start`, and macro-agent events.

Two small production fixes were necessary to satisfy the brief's scenario:

- Added `AGENT_REVIEW -> BLOCKED` transition in the state machine.
- Added a `state_change` audit log entry for `READY -> RUNNING` in `_trigger_execution`.

## Test Summary

- `uv run pytest tests/test_phase1_smoke.py -v` — 1 passed
- `uv run pytest tests/ -q` — 116 passed, 1 warning
- `uv run ruff check governance_controller tests` — All checks passed

## Concerns

None.
