# Task 9 Brief: Harness Provider Registry

**Goal:** Implement a registry of supported agent harnesses, with OpenCode as the primary provider and Claude Code as a subscription-based alternative.

**Files to create:**
- `src/governance_controller/governance_controller/harness/base.py`
- `src/governance_controller/governance_controller/harness/opencode.py`
- `src/governance_controller/governance_controller/harness/claude_code.py`
- `src/governance_controller/governance_controller/harness/registry.py`
- `src/governance_controller/tests/test_harness_registry.py`

**Files to modify:**
- `src/governance_controller/governance_controller/harness/__init__.py` (re-export)

**Exact values to use verbatim:**

`HarnessProvider` fields:
- `name: str`
- `command: str`
- `auth: str` — either `"subscription"` or `"provider-configured"`
- `supports_mcp: bool = True`
- `allowed_roles: list[str] = ["worker", "reviewer", "planner"]`

Registered providers:
- `opencode`: command `"opencode"`, auth `"provider-configured"`, supports_mcp=True
- `claude-code`: command `"claude"`, auth `"subscription"`, supports_mcp=True

**Interfaces produced:**
- `HarnessProvider` from `governance_controller.harness.base`
- `registry` singleton from `governance_controller.harness.registry` with methods:
  - `register(provider: HarnessProvider) -> None`
  - `get(name: str) -> HarnessProvider` (raises `KeyError` if missing)
  - `list() -> list[str]`
  - `is_registered(name: str) -> bool`

**Interfaces consumed:**
- None.

**Constraints:**
- Credentials must never appear in provider YAML/Python files — only auth type.
- Registry is initialized with OpenCode and Claude Code at import time.
- No actual harness spawning in this task; only metadata registry.

**Verification steps:**
1. Write `tests/test_harness_registry.py` covering:
   - Registry contains `"opencode"` and `"claude-code"`.
   - `get("opencode")` returns provider with command `"opencode"`.
   - `get("unknown")` raises `KeyError`.
   - `is_registered` returns correct boolean.
2. Run `uv run pytest tests/test_harness_registry.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add harness provider registry with OpenCode and Claude Code`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-9-report.md`
