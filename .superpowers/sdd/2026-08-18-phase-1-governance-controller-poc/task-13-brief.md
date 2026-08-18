# Task 13 Brief: Minimal Event Bridge Listener

**Goal:** Create a small service that translates macro-agent workspace events into Controller state updates and audit logs. Phase 1 version is in-process only (no separate deployment).

**Files to create:**
- `src/governance_controller/governance_controller/adapters/macro_agent/event_bridge.py`
- `src/governance_controller/tests/test_event_bridge.py`

**Exact values to use verbatim:**

Event-to-state mapping:
- `worktree:allocated` → `RUNNING` (if task was READY)
- `landing:started` → `RUNNING` (if task was RUNNING)
- `landing:completed` → `AGENT_REVIEW`
- `conflict:created` → `BLOCKED`
- `conflict:resolved` → `RUNNING`
- `stream:abandoned` → `FAILED`

`EventBridge.handle(db, event: dict) -> None`:
1. Extract `event_type` from `event["type"]`.
2. Extract `task_id` from `event["metadata"]["controller_task_id"]`.
3. If `event_type` not in mapping, ignore (but still audit log with type `macro_agent_other`).
4. Load task by id.
5. If task exists and transition to target state is valid, use `StateMachine.transition` and update DB.
6. Log audit event `macro_agent_<event_type>` with payload.

**Interfaces produced:**
- `EventBridge` from `governance_controller.adapters.macro_agent.event_bridge`

**Interfaces consumed:**
- `Task` from `governance_controller.models.task`
- `StateMachine` from `governance_controller.services.state_machine`
- `AuditService` from `governance_controller.services.audit_service`
- `TaskState`, `_EVENT_TO_STATE` mapping

**Constraints:**
- In-process implementation; no WebSocket/HTTP listener in Phase 1.
- Idempotent: if the same event is handled twice and task already in target state, no error.
- If task not found, log audit event but do not raise.

**Verification steps:**
1. Write `tests/test_event_bridge.py` covering:
   - `landing:completed` transitions RUNNING → AGENT_REVIEW.
   - `conflict:created` transitions RUNNING → BLOCKED.
   - unknown event logged but state unchanged.
   - missing task_id does not raise.
2. Run `uv run pytest tests/test_event_bridge.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add minimal macro-agent event bridge listener`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-13-report.md`
