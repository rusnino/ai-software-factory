# GAP-021 Closure Report

## Status
CLOSED

## Commit Hash
`5b157b8`

## What Was Done
Added an automated regression test in `src/governance_controller/tests/test_event_bridge.py` that exercises a `TaskContract` with a `CompletionContract` through `EventBridge.handle()`.

### Test scenario
- Creates a `Task` in `RUNNING` state with a `task_contract_json` containing a `CompletionContract`.
- The `CompletionContract` declares one required check: `command="exit 1"`, `expect_exit=0`.
- Sends a `landing:completed` macro-agent event for the task.
- Asserts the task transitions to `FAILED` (not `HUMAN_REVIEW`).
- Asserts a `verification_failed` audit log entry is recorded by the verification service, with the required check marked `failed`.

## Verification
```
uv run pytest tests/ -q
155 passed in 2.82s

uv run ruff check governance_controller tests
All checks passed!
```

## Concerns
- The fix is test-only; no production code changes were needed because the failure-gating path was already correctly implemented in `EventBridge.handle()` and `VerificationService.verify_and_advance()`.
- This test catches regressions in the `landing:completed → FAILED` path but does not cover the success path (`AGENT_REVIEW → HUMAN_REVIEW`) through `EventBridge.handle()`, which remains a candidate for a follow-up test.
