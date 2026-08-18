# Task 9 Report: Harness Provider Registry

## Status

Completed.

## Commit

- `7195140 feat: add harness provider registry with OpenCode and Claude Code`

## Files Added

- `src/governance_controller/governance_controller/harness/base.py`
- `src/governance_controller/governance_controller/harness/opencode.py`
- `src/governance_controller/governance_controller/harness/claude_code.py`
- `src/governance_controller/governance_controller/harness/registry.py`
- `src/governance_controller/tests/test_harness_registry.py`

## Files Modified

- `src/governance_controller/governance_controller/harness/__init__.py` (re-exports `HarnessProvider` and `registry`)

## Implementation Summary

- `HarnessProvider` is a dataclass with `name`, `command`, `auth`, `supports_mcp`, and `allowed_roles` fields.
- Providers are initialized with metadata only; no credentials are stored in source files.
- The registry singleton is populated at import time with:
  - `opencode`: command `opencode`, auth `provider-configured`
  - `claude-code`: command `claude`, auth `subscription`
- Registry API: `register`, `get`, `list`, `is_registered`.
- No harness spawning logic was added per the task constraints.

## Test Summary

- `tests/test_harness_registry.py` added and passing (5/5).
- Full test suite: 60 passed, 1 warning (pre-existing Pydantic config deprecation).
- Ruff: all checks passed.

## Concerns

None.
