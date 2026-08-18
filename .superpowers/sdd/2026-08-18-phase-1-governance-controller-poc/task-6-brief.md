# Task 6 Brief: Audit Log

**Goal:** Implement an append-only Audit Log model and service, and wire it into the Approval Service.

**Files to create:**
- `src/governance_controller/governance_controller/models/audit_log.py`
- `src/governance_controller/governance_controller/services/audit_service.py`
- `src/governance_controller/tests/test_audit_log.py`

**Files to modify:**
- `src/governance_controller/governance_controller/services/approval_service.py` (replace temporary/no-op audit logging with real AuditService calls)
- `src/governance_controller/governance_controller/models/__init__.py` (re-export AuditLog)

**Exact values to use verbatim:**

`AuditLog` model fields:
- `id: int | None = None` (primary key)
- `event_id: str` (index, UUID)
- `event_type: str`
- `task_id: str` (index)
- `execution_id: str | None = None`
- `actor: str`
- `source: str`
- `timestamp: datetime = utcnow`
- `payload: dict[str, Any] = {}` (sa_type=JSON or JSONB)

AuditService logging events in approval_service.py (wire these):
- On every approval: `event_type="approval"`, payload with previous_state and new_state.
- On state transitions driven by approval_service: `event_type="state_change"`, payload with previous_state and new_state.

**Interfaces produced:**
- `AuditLog` SQLModel from `governance_controller.models.audit_log`
- `AuditService.log(db, event_type, task_id, actor, source, execution_id=None, payload=None) -> AuditLog`

**Interfaces consumed:**
- ApprovalService should call `AuditService.log(...)` after successful approval and state transition.

**Constraints:**
- Append-only: never update or delete audit log entries.
- Use UUID for `event_id`.
- Async SQLAlchemy.
- Add structlog logging side effect.

**Verification steps:**
1. Write `tests/test_audit_log.py` covering:
   - Creating an audit log entry.
   - Verifying `event_id` is generated.
   - Verifying append-only behavior (no update path exposed).
   - Verifying approval_service writes audit entries on approval.
2. Run `uv run pytest tests/test_audit_log.py tests/test_approval_service.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add append-only AuditLog and AuditService; wire into ApprovalService`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-6-report.md`
