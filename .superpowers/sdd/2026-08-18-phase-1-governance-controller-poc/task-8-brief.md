# Task 8 Brief: Plane Adapter Interface + Stub

**Goal:** Define an abstract Plane Adapter interface and provide an in-memory stub implementation for Phase 1, so the Controller has zero runtime dependency on Plane CE.

**Files to create:**
- `src/governance_controller/governance_controller/adapters/plane_adapter.py`
- `src/governance_controller/governance_controller/adapters/plane_adapter_memory.py`
- `src/governance_controller/tests/test_plane_adapter.py`

**Files to modify:**
- `src/governance_controller/governance_controller/adapters/__init__.py` (re-export)

**Exact values to use verbatim:**

Abstract interface methods:
- `sync_task_state(task_id: str, state: str) -> None`
- `sync_task_fields(task_id: str, fields: dict) -> None`
- `add_comment(task_id: str, text: str) -> None`

In-memory stub stores all calls in `self.updates: list[dict]`.

**Interfaces produced:**
- `PlaneAdapter(ABC)` from `governance_controller.adapters.plane_adapter`
- `MemoryPlaneAdapter(PlaneAdapter)` from `governance_controller.adapters.plane_adapter_memory`

**Interfaces consumed:**
- None.

**Constraints:**
- Plane adapter is not used by approval service in Phase 1; it exists as an injectable interface only.
- Memory stub must record every sync and comment for assertions.
- Must not make any HTTP calls or import Plane SDK.

**Verification steps:**
1. Write `tests/test_plane_adapter.py` covering:
   - `MemoryPlaneAdapter` records `sync_task_state`.
   - `MemoryPlaneAdapter` records `add_comment`.
   - `MemoryPlaneAdapter` records `sync_task_fields`.
2. Run `uv run pytest tests/test_plane_adapter.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add PlaneAdapter interface and in-memory stub`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-8-report.md`
