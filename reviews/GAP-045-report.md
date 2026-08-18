# GAP-045 Report

## Summary
`TaskContract.verification["commands"]` were policy-validated by `PolicyEngine` but never executed when a `CompletionContract` was absent. The no-completion_contract branch in `VerificationService.verify_execution()` reported fake `"schema"` / `"syntax"` passes and only checked whether the proposer's own `forbidden_paths` list literally contained `__pycache__` or `.env`.

## Changes
- `src/governance_controller/governance_controller/services/verification_service.py`
  - `_verification_commands_from_contract()` is now called unconditionally.
  - Shell commands declared in `TaskContract.verification.commands` run and gate the verification result regardless of `completion_contract` presence.
  - Removed hardcoded `"schema"` / `"syntax"` passes.
  - In the no-completion_contract branch, `contract.forbidden_paths` is now evaluated as a real prefix-based forbidden-path check against `inputs` + `deliverables`.
  - Updated docstring to reflect the new behavior.
- `src/governance_controller/tests/test_verification_service.py`
  - Updated existing tests that relied on fake checks.
  - Added tests proving `verification.commands` run and gate state transition without `completion_contract`.
  - Added a test proving `verification.commands` merge correctly when both fields are present.
- `reviews/GAPS.md`
  - Marked GAP-045 `CLOSED` with commit hash `8c1b8a1`.

## Verification
```bash
uv run pytest tests/ -q
uv run ruff check governance_controller tests
uv run mypy --strict governance_controller
```

Results:
- `159 passed in 2.27s`
- `All checks passed!`
- `Success: no issues found in 49 source files`

## Commit
`8c1b8a12b9640f7b970c550200f9786f3caa5397`
Message: `fix(GAP-045): execute TaskContract.verification.commands even without CompletionContract`

## Concerns
- The no-completion_contract forbidden_paths check now uses the proposer-supplied `TaskContract.forbidden_paths` verbatim. This is consistent with the original intent but places trust in the contract; catastrophic path enforcement is the responsibility of `PolicyEngine` against the project profile before approval.
- `TaskContract.verification` is still an untyped `dict[str, Any]`. A future schema upgrade should replace it with a structured model, but that is outside the scope of this gap closure.
- As noted in GAP-046, downstream transitions from `verify_and_advance` still use unguarded `StateMachine.transition()`; that gap remains `OPEN`.
