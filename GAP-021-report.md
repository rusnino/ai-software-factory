# GAP-021 Closure Report

## Status
CLOSED

## Commit Hash
`9d0bd32`

## What Was Done
Added automated regression tests in `src/governance_controller/tests/test_event_bridge.py` that exercise a `TaskContract` with a `CompletionContract` through `EventBridge.handle()` for both the failing and passing verification paths.

### Failing-path test scenario
- Creates a `Task` in `RUNNING` state with a `task_contract_json` containing a `CompletionContract`.
- The `CompletionContract` declares one required check: `command="exit 1"`, `expect_exit=0`.
- Sends a `landing:completed` macro-agent event for the task.
- Asserts the task transitions to `FAILED` (not `HUMAN_REVIEW`).
- Asserts a `verification_failed` audit log entry is recorded by the verification service, with the required check marked `failed`.

### Passing-path test scenario
- Creates a `Task` in `RUNNING` state with a `task_contract_json` containing a `CompletionContract`.
- The `CompletionContract` declares one required check: `command="exit 0"`, `expect_exit=0`.
- Sends a `landing:completed` macro-agent event for the task.
- Asserts the task transitions from `RUNNING` through `AGENT_REVIEW` to `HUMAN_REVIEW`.
- Asserts a `verification_passed` audit log entry is recorded by the verification service, with the required check marked `passed`.

## Verification
```
uv run pytest tests/ -q
143 passed in 2.56s

uv run ruff check governance_controller tests
All checks passed!

uv run mypy --strict governance_controller
Success: no issues found in 49 source files
```

## Concerns
- The fix is test-only; no production code changes were needed because the gating paths were already correctly implemented in `EventBridge.handle()` and `VerificationService.verify_and_advance()`.
- The GAPS.md row for GAP-021 has been updated from `IN_PROGRESS` to `CLOSED` and references the closing commit hash.
- No other open concerns.
