# Gap Ledger

Running, cross-review ledger of every finding raised in `reviews/REVIEW-NNN-*.md` files. See `AGENTS.md` §
"Review Findings and Gap Tracking" for the convention this file follows. Rows are never deleted — a `CLOSED`
row is proof a gap was found and fixed, not evidence it can be forgotten.

**Status values:** `OPEN` (not yet addressed) · `IN_PROGRESS` (someone is actively working it) · `CLOSED` (fixed,
with commit hash) · `ACCEPTED` (consciously deferred with a documented reason — only valid for `MEDIUM`/`LOW`
severity; `CRITICAL`/`HIGH` gaps may not be marked `ACCEPTED` in lieu of fixing them without also revising the
source SPEC/ADR that they violate).

**Gate rule:** no phase may be declared complete in `docs/NEXT_STEPS.md` or have its `specs/SPEC-10-phase-plan.md`
checklist ticked while any `CRITICAL` or `HIGH` row tied to that phase is still `OPEN`.

| Gap ID | Severity | Summary | Source | File(s) | Status | Closed By |
|---|---|---|---|---|---|---|
| GAP-001 | CRITICAL | Self-approval prevention unenforced: `PermissionService` exists but is never called by `ApprovalService`/`POST /approvals`; no field records who proposed a task, so self-approval can't even be detected once wired | REVIEW-001 | `services/permission_service.py`, `services/approval_service.py`, `api/approvals.py`, `models/task.py` | CLOSED | `08f1171` |
| GAP-002 | CRITICAL | `PolicyEngine` forbidden-path check was inverted (fixed in `94444b2`), but the replacement checked exact string equality only. Now uses path-prefix/containment comparison, so `~/.ssh/id_rsa` is rejected when `~/.ssh` is forbidden | REVIEW-001, REVIEW-002 | `services/policy_engine.py` | CLOSED | `14d11da` |
| GAP-003 | HIGH | `Idempotency-Key` HTTP header (SPEC-03 §3.3 primary mechanism) is never read; endpoint always computes the fallback synthetic key regardless of whether a header was supplied | REVIEW-001 | `api/approvals.py:41-46` | CLOSED | `70948bb` |
| GAP-004 | HIGH | The (synthetic or real) idempotency key is never used for approval dedup lookup — dedup is keyed on `(task_id, approval_type, actor)` only, so distinct approval attempts can silently collapse into a no-op | REVIEW-001 | `services/approval_service.py:99-105` | CLOSED | `70948bb` |
| GAP-005 | HIGH | Response status codes don't match SPEC-03 §3.3: policy violations return 422 instead of 403; 400 is never emitted; a malformed `timestamp` raises an unguarded exception → unhandled 500 | REVIEW-001 | `api/approvals.py:28,109-112` | CLOSED | `fd6501b` |
| GAP-006 | HIGH | `2edda61` wired `CompletionContract`'s static checks, but `required`/`optional` commands were never executed and `VerificationService` was not called from any live path. Now `VerificationService` executes commands via subprocess, compares exit codes, and is invoked by `EventBridge.handle()` on `landing:completed` to gate `AGENT_REVIEW → HUMAN_REVIEW` | REVIEW-001, REVIEW-002 | `services/verification_service.py`, `adapters/macro_agent/event_bridge.py` | CLOSED | `116d95a` |
| GAP-007 | HIGH | Most `ProjectProfile` security/git fields (`docker_socket`, `destructive_shell`, `spawn_subagents`, `network`, `force_push`, `signed_commits`) are schema-only and never enforced by `PolicyEngine` | REVIEW-001 | `schemas/project_profile.py`, `services/policy_engine.py` | CLOSED | `ae766eb` |
| GAP-008 | HIGH | `c1195d8`'s "reconciliation stub" was orphaned dead code: never invoked by anything, compared against inert never-populated data, and implemented ~1 of SPEC-03 §3.9's 5 requirements. Removed and explicitly deferred to Phase 2 in `docs/NEXT_STEPS.md` until Plane and opentasks are real integrations | REVIEW-001, REVIEW-002 | `services/reconciliation_service.py`, `docs/NEXT_STEPS.md` | CLOSED | `ff10d89` |
| GAP-009 | MEDIUM | `5af189c`'s `docs/DEPENDENCIES.md` falsely claimed `structlog`+`python-json-logger` were paired via a JSON console processor. Dropped the unused `python-json-logger` dependency and corrected the doc | REVIEW-001, REVIEW-002 | `src/governance_controller/docs/DEPENDENCIES.md`, `pyproject.toml` | CLOSED | `4b3411c` |
| GAP-010 | MEDIUM | `GET /executions/{id}` always returns 501 with a docstring claiming execution records aren't persisted yet — false, `Execution` rows are created by `ApprovalService._trigger_execution` | REVIEW-001 | `api/executions.py` | CLOSED | `d67df76` |
| GAP-011 | MEDIUM | `docs/NEXT_STEPS.md` claims an "audit-log endpoint" exists; no such HTTP route exists anywhere — only the internal `AuditService`/`AuditLog` model | REVIEW-001 | `docs/NEXT_STEPS.md`, `api/` | CLOSED | `e28c152` |
| GAP-012 | MEDIUM | `aa6a82b` added real `AuditService.log` calls on task/profile creation, but `project_profile_updated` fired unconditionally and had no tests. Now only logs `project_profile_updated` when profile content changes, and tests cover task creation, profile creation, idempotent reuse, and content-change updates | REVIEW-001, REVIEW-002 | `services/task_service.py`, `tests/test_task_service_audit.py` | CLOSED | `fa94ad5` |
| GAP-013 | MEDIUM | `31b6dfa` ticked the checklist with honest footnoted deferrals for 5 of 6 items, but ticked "Failed verification never produces DONE" with no disclosure that `VerificationService` is unwired (see GAP-006). Corrected directly in REVIEW-002 (line unchecked + footnote added) | REVIEW-001, REVIEW-002 | `specs/SPEC-10-phase-plan.md` | CLOSED | REVIEW-002 (direct doc fix) |
| GAP-014 | MEDIUM | `EventBridge.handle()` silently swallows invalid-transition errors (`suppress(ValueError)`) and logs an audit entry indistinguishable from a successful transition, reducing audit fidelity | REVIEW-001 | `adapters/macro_agent/event_bridge.py` | CLOSED | `ca838c8` |
| GAP-015 | LOW | `test_placeholder.py` is vestigial scaffold (`assert True`), safe to delete now that real tests exist | REVIEW-001 | `tests/test_placeholder.py` | CLOSED | `d05dfb5` |
| GAP-016 | LOW | `08f1171` committed the 23 previously-untracked task brief/report files, but `progress.md` only checked off Tasks 1-2 and didn't list Tasks 17-20. Updated `progress.md` with all 20 tasks and the review gap-closure follow-ups | REVIEW-001, REVIEW-002 | `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/progress.md` | CLOSED | `c088c7e` |
| GAP-017 | LOW | `Settings` uses deprecated Pydantic v1-style `class Config:` instead of `model_config = SettingsConfigDict(...)` | REVIEW-001 | `governance_controller/config.py` | CLOSED | `060c7c8` |
| GAP-018 | LOW | Hardcoded local-dev DB credentials (`postgres:postgres`, `controller:controller`) with no comment marking them as dev-only defaults | REVIEW-001 | `governance_controller/config.py`, `docker-compose.yml` | CLOSED | `b996ad1` |
| GAP-019 | LOW | `api/__init__.py` re-exports `approvals`/`executions`/`tasks` but not `health`; cosmetic only, no functional bug | REVIEW-001 | `api/__init__.py` | CLOSED | `e28c152` |
