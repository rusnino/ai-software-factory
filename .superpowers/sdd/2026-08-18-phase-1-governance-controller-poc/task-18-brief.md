# Task 18 Brief: Security / Permission Model Stub

**Goal:** Add a minimal permission service that validates whether an actor is allowed to request a given approval type for a task. Phase 1 uses a simple allow-list stub.

**Files to create:**
- `src/governance_controller/governance_controller/services/permission_service.py`
- `src/governance_controller/tests/test_permission_service.py`

**Exact values to use verbatim:**

`PermissionService`:
- `__init__(self, admins: set[str] | None = None)`
- `async def may_approve(self, actor: str, task_id: str, approval_type: ApprovalType) -> bool`

Default admins: `{"admin"}`.

Rules:
- Any actor may request `PLAN` approval.
- Only admins may request `EXECUTION` approval.
- Only admins may request `MERGE` approval.
- `actor` `"system"` or `"agent"` may never approve (return `False`).

**Interfaces produced:**
- `PermissionService` from `governance_controller.services.permission_service`

**Interfaces consumed:**
- `ApprovalType` from `governance_controller.constants`

**Constraints:**
- Keep it synchronous logic inside async method for interface compatibility.
- No RBAC/OPA integration in Phase 1.

**Verification steps:**
1. Write `tests/test_permission_service.py` covering:
   - non-admin can approve plan.
   - non-admin cannot approve execution/merge.
   - admin can approve execution/merge.
   - system/agent cannot approve anything.
2. Run `uv run pytest tests/test_permission_service.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add minimal permission service stub`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-18-report.md`
