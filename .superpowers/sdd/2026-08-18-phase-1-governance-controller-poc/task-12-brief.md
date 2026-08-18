# Task 12 Brief: opentasks Materialization Stub

**Goal:** Add a service that materializes the approved Plane task subgraph into a runtime task graph for opentasks. On Phase 1 this is a stub that validates DAG structure and produces placeholder runtime IDs.

**Files to create:**
- `src/governance_controller/governance_controller/services/opentasks_service.py`
- `src/governance_controller/tests/test_opentasks_service.py`

**Exact values to use verbatim:**

`OpenTasksService.materialize(task_contract: TaskContract) -> dict`:
```python
return {
    "runtime_tasks": [
        {
            "plane_task_id": task_contract.task_id,
            "opentasks_id": f"OT-{task_contract.task_id}",
            "dependencies": task_contract.dependencies,
        }
    ]
}
```

`OpenTasksService.validate_dependencies(task_contracts: list[TaskContract]) -> None`:
- Ensure all `dependencies` reference existing `task_id`s in the list.
- Ensure no cycles (raise `ValueError` if cycle detected).

**Interfaces produced:**
- `OpenTasksService` from `governance_controller.services.opentasks_service`

**Interfaces consumed:**
- `TaskContract` from `governance_controller.schemas.task_contract`

**Constraints:**
- No real opentasks daemon integration in Phase 1.
- Materialization happens inside ApprovalService after `EXEC_APPROVED`.
- Do not persist runtime graph in DB in Phase 1.

**Verification steps:**
1. Write `tests/test_opentasks_service.py` covering:
   - `materialize` returns expected runtime task.
   - `validate_dependencies` passes for valid acyclic graph.
   - `validate_dependencies` raises on missing dependency.
   - `validate_dependencies` raises on cycle.
2. Run `uv run pytest tests/test_opentasks_service.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add opentasks materialization stub`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-12-report.md`
