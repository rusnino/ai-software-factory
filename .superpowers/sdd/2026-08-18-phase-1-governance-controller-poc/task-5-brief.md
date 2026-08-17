# Task 5 Brief: Approval Service and Policy Engine

**Goal:** Implement embedded Policy Engine and Approval Service that enforce Task Contract, Project Profile, approval chain, and harness allowlist before recording approval and advancing state.

**Files to create:**
- `src/governance_controller/governance_controller/services/policy_engine.py`
- `src/governance_controller/governance_controller/services/approval_service.py`
- `src/governance_controller/governance_controller/models/approval.py`
- `src/governance_controller/tests/test_policy_engine.py`
- `src/governance_controller/tests/test_approval_service.py`

**Files to modify:**
- `src/governance_controller/governance_controller/models/__init__.py` (re-export)

**Exact values to use verbatim:**

`Approval` model fields:
- `id: int | None = None` (primary key)
- `task_id: str` (index)
- `approval_type: ApprovalType`
- `source: str`
- `actor: str`
- `timestamp: datetime = utcnow`
- `comment: str | None = None`
- `idempotency_key: str` (index)

Policy Engine checks:
1. Task Contract completeness: `objective` non-empty, `acceptance` non-empty.
2. Project Profile constraints: requested harness in `execution.allowed_harnesses`.
3. Forbidden paths: any path in `contract.forbidden_paths` that also appears in `profile.security.forbidden_paths` is flagged.
4. Required human approval: embedded in workflow via approval_type gating.
5. Harness allowlist: must match `profile.execution.allowed_harnesses`.
6. Approval chain correctness: `execution` approval requires `PLAN_APPROVED` state; `merge` approval requires `HUMAN_REVIEW` state. The state machine enforces this, so approval_service only needs to validate policy.

Idempotency:
- Primary key: `(task_id, approval_type, actor)` plus uniqueness on `idempotency_key`.
- If an identical `(task_id, approval_type, actor)` approval already exists, return the existing task state (idempotent).

Exact approval type → target state mapping:
- `plan` → `PLAN_APPROVED`
- `execution` → `EXEC_APPROVED`
- `merge` → `DONE`

**Interfaces produced:**
- `PolicyEngine.evaluate(contract, profile, approval_type) -> PolicyResult`
- `PolicyResult(allowed: bool, violations: list[str])`
- `ApprovalService(db, policy_engine=None)` with method
  `approve(task, contract, profile, approval_type, source, actor, idempotency_key, comment=None) -> Task`

**Interfaces consumed:**
- `Task` from `governance_controller.models.task`
- `TaskState`, `ApprovalType` from `governance_controller.constants`
- `TaskContract` from `governance_controller.schemas.task_contract`
- `ProjectProfile` from `governance_controller.schemas.project_profile`
- `StateMachine` from `governance_controller.services.state_machine`
- `AuditService` from `governance_controller.services.audit_service` (will be created in Task 6; for now, use a temporary inline no-op or simple print, then wire after Task 6)

**Constraints:**
- ApprovalService must be async and use SQLAlchemy AsyncSession.
- Policy evaluation must happen BEFORE recording approval or advancing state.
- If policy fails, raise `ValueError` with violation list; no state change, no approval record.
- Audit logging is optional in this task; wire properly in Task 6.

**Verification steps:**
1. Write `tests/test_policy_engine.py` covering:
   - valid execution approval passes
   - forbidden harness rejected
   - missing acceptance criteria rejected
   - forbidden path conflict rejected
2. Write `tests/test_approval_service.py` covering:
   - plan approval advances state to `PLAN_APPROVED`
   - execution approval advances state to `EXEC_APPROVED`
   - merge approval advances state to `DONE`
   - idempotent second approval returns same state without duplicate record
   - policy violation raises ValueError
3. Run `uv run pytest tests/test_policy_engine.py tests/test_approval_service.py -v`.
4. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add PolicyEngine and ApprovalService`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-5-report.md`
