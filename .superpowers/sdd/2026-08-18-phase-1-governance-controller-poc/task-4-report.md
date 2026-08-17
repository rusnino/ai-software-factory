# Task 4 Report: Task Contract / Project Profile / Completion Contract Schemas

## Status

Completed.

## Files Changed

- Created `src/governance_controller/governance_controller/schemas/task_contract.py`
- Created `src/governance_controller/governance_controller/schemas/project_profile.py`
- Created `src/governance_controller/governance_controller/schemas/completion_contract.py`
- Created `src/governance_controller/tests/test_schemas.py`
- Modified `src/governance_controller/governance_controller/schemas/__init__.py`

## Implementation Notes

- Pydantic v2 `BaseModel` models defined for each schema with exact default values required by the brief.
- `ExecutionConfig` is defined separately in `task_contract.py` (task-level team/harness/max_retries) and `project_profile.py` (project-level allowed_harnesses/sandbox/max_parallel_agents). Only the task-level `ExecutionConfig` is re-exported from the package root to avoid name collisions; the project profile model uses its module-local `ExecutionConfig`.
- All schema public names are re-exported from `governance_controller.schemas`.

## Test Summary

- Ran `uv run pytest tests/test_schemas.py -v`: 4 passed.
- Ran `uv run ruff check governance_controller tests`: all checks passed.

## Commit

- `feat: add TaskContract, ProjectProfile, CompletionContract schemas`

## Concerns

None.
