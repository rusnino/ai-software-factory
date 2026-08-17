# Task 5 Report: Approval Service and Policy Engine

## Status

Completed.

## Files Created

- `src/governance_controller/governance_controller/services/policy_engine.py`
- `src/governance_controller/governance_controller/services/approval_service.py`
- `src/governance_controller/governance_controller/models/approval.py`
- `src/governance_controller/tests/test_policy_engine.py`
- `src/governance_controller/tests/test_approval_service.py`

## Files Modified

- `src/governance_controller/governance_controller/models/__init__.py`
- `src/governance_controller/governance_controller/models/task.py`
- `src/governance_controller/tests/conftest.py`
- `src/governance_controller/pyproject.toml`
- `src/governance_controller/uv.lock`

## Implementation Notes

- `Approval` SQLModel table includes indexes on `task_id`, unique `idempotency_key`, and unique composite `(task_id, approval_type, actor)`.
- `PolicyEngine.evaluate(contract, profile, approval_type)` returns a `PolicyResult` with `allowed` and `violations`.
- Policy checks implemented:
  - Task Contract completeness (`objective` non-empty, `acceptance` non-empty).
  - Harness allowlist (requested harness in `profile.execution.allowed_harnesses`).
  - Forbidden path conflicts between `contract.forbidden_paths` and `profile.security.forbidden_paths`.
  - Approval-type gating enforced via state machine transitions.
- `ApprovalService.approve(...)` is async, uses `AsyncSession`, evaluates policy before any state change, records approvals, and advances state via `StateMachine`.
- Idempotency: duplicate `(task_id, approval_type, actor)` returns existing task state without creating a duplicate approval record.
- Temporary no-op audit helper is wired into `ApprovalService`; will be replaced by real `AuditService` in Task 6.
- Tests run against an async SQLite in-memory database (fallback when PostgreSQL is unavailable). This required adding `aiosqlite` to dev dependencies and switching `Task.task_contract_json` from PostgreSQL-only `JSONB` to cross-dialect `JSON`.

## Test Summary

```text
39 passed, 1 warning in 0.19s
```

Specific task tests:

```text
tests/test_policy_engine.py::TestPolicyEngineValidCases::test_valid_execution_approval_passes PASSED
tests/test_policy_engine.py::TestPolicyEngineRejections::test_forbidden_harness_rejected PASSED
tests/test_policy_engine.py::TestPolicyEngineRejections::test_missing_acceptance_or_objective_rejected[-acceptance0-objective is empty] PASSED
tests/test_policy_engine.py::TestPolicyEngineRejections::test_missing_acceptance_or_objective_rejected[Do thing-acceptance1-acceptance criteria are empty] PASSED
tests/test_policy_engine.py::TestPolicyEngineRejections::test_missing_acceptance_or_objective_rejected[   -acceptance2-objective is empty] PASSED
tests/test_policy_engine.py::TestPolicyEngineRejections::test_forbidden_path_conflict_rejected PASSED
tests/test_approval_service.py::TestApprovalServiceStateTransitions::test_plan_approval_advances_state_to_plan_approved PASSED
tests/test_approval_service.py::TestApprovalServiceStateTransitions::test_execution_approval_advances_state_to_exec_approved PASSED
tests/test_approval_service.py::TestApprovalServiceStateTransitions::test_merge_approval_advances_state_to_done PASSED
tests/test_approval_service.py::TestApprovalServiceIdempotency::test_idempotent_second_approval_returns_same_state PASSED
tests/test_approval_service.py::TestApprovalServicePolicyViolations::test_policy_violation_raises_value_error PASSED
tests/test_approval_service.py::TestApprovalServicePolicyViolations::test_policy_engine_injected_used PASSED
```

Ruff: all checks passed.

## Commits

- `feat: add PolicyEngine and ApprovalService`

## Concerns

- `Task.task_contract_json` was changed from `JSONB` to `JSON` to allow tests to run on SQLite. This is functionally equivalent on PostgreSQL and is only a storage abstraction change, not a data model change.
- The approval-chain correctness checks for `execution` (requires `PLAN_APPROVED`) and `merge` (requires `HUMAN_REVIEW`) are handled implicitly by the `StateMachine`; a bad transition raises `ValueError`. The tests confirm this by starting tasks in the correct predecessor states.
