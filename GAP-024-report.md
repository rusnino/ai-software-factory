# GAP-024 Fix Report

## Status

CLOSED by commit `b143f5f` on branch `fix/gap024-verification-commands-policy`.

## Summary

`TaskContract.verification["commands"]` was merged into `VerificationService` execution without any policy validation. A contract could pass `PolicyEngine.evaluate()` with a benign `completion_contract` while carrying malicious commands in `verification.commands`, which were then executed raw via `asyncio.create_subprocess_shell`.

## Changes Made

- `src/governance_controller/governance_controller/services/policy_engine.py`
  - Extracted shared command-validation + profile cross-check logic into `_validate_command_against_profile()`.
  - Refactored `_validate_completion_contract_commands()` to use the new helper.
  - Added `_validate_verification_commands()` which iterates `contract.verification.get("commands", [])` and applies the same normalization, forbidden-token, privilege-escalation, destructive-shell, and docker-socket profile checks as completion-contract commands.
  - Wired `_validate_verification_commands()` into `PolicyEngine.evaluate()` immediately after completion-contract validation.

- `src/governance_controller/tests/test_policy_engine.py`
  - Added `verification` kwarg to the `_make_contract()` helper.
  - Added `TestPolicyEngineVerificationCommandsAllowlist` covering:
    - safe verification command passes;
    - malicious command (`echo ok; rm -rf /`) rejected;
    - `sudo` privilege escalation rejected;
    - docker-socket command rejected when profile denies;
    - destructive command rejected when profile denies;
    - destructive command allowed when profile explicitly permits.

## Decisions & Notes

- `VerificationService` itself was intentionally left unchanged: its job is to execute the merged check list. The authoritative policy gate is `PolicyEngine.evaluate()` (per SPEC-03 and the Non-Negotiable Architecture Rules). Requiring policy validation at approval time prevents thegap at the source rather than duplicating enforcement inside the executor.
- The fix reuses the existing `_normalize_and_validate_command` logic and the same profile cross-checks for `docker_socket` and `destructive_shell`, satisfying the task requirement to treat `verification.commands` identically to `CompletionContract` commands.
- No new external dependencies were added.

## Verification

```bash
cd src/governance_controller
uv run pytest tests/ -q
uv run ruff check governance_controller tests
uv run mypy --strict governance_controller
```

Results (run in worktree `/root/projects/ai-software-factory/.worktrees/gap024b`):

- `pytest`: 148 passed
- `ruff check`: All checks passed!
- `mypy --strict`: Success: no issues found in 49 source files

## Concerns

- `VerificationService` still executes commands via `asyncio.create_subprocess_shell`, which uses a shell. Even with policy validation, this remains a high-risk execution surface. Future hardening (e.g., execve-style direct execution, Docker/Firecracker sandboxing per SPEC-08) is recommended.
- Commands in `verification["commands"]` are coerced into `Check` objects with `expect_exit=0`. A non-zero expected-exit cannot be expressed in this field; this is a schema limitation, not a security bug, but may warrant a SPEC-03 clarification.
- The `PolicyEngine` denylist is regex/substring based and cannot catch all forms of shell obfuscation. Defense in depth (sandboxing) remains Phase 1's stated future work.
