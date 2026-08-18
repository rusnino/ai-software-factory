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
| GAP-001 | CRITICAL | Self-approval prevention unenforced: `PermissionService` exists but is never called by `ApprovalService`/`POST /approvals`; no field records who proposed a task, so self-approval can't even be detected once wired | REVIEW-001 | `services/permission_service.py`, `services/approval_service.py`, `api/approvals.py`, `models/task.py` | OPEN | |
| GAP-002 | CRITICAL | `PolicyEngine` forbidden-path check is inverted — rejects when Task Contract and Project Profile *agree* on a forbidden path (set intersection) instead of checking the task touches one; breaks approval for SPEC-03's own canonical example YAMLs | REVIEW-001 | `services/policy_engine.py:56-60` | OPEN | |
| GAP-003 | HIGH | `Idempotency-Key` HTTP header (SPEC-03 §3.3 primary mechanism) is never read; endpoint always computes the fallback synthetic key regardless of whether a header was supplied | REVIEW-001 | `api/approvals.py:41-46` | OPEN | |
| GAP-004 | HIGH | The (synthetic or real) idempotency key is never used for approval dedup lookup — dedup is keyed on `(task_id, approval_type, actor)` only, so distinct approval attempts can silently collapse into a no-op | REVIEW-001 | `services/approval_service.py:99-105` | OPEN | |
| GAP-005 | HIGH | Response status codes don't match SPEC-03 §3.3: policy violations return 422 instead of 403; 400 is never emitted; a malformed `timestamp` raises an unguarded exception → unhandled 500 | REVIEW-001 | `api/approvals.py:28,109-112` | OPEN | |
| GAP-006 | HIGH | `CompletionContract` schema (SPEC-03 §3.7) is defined but never consumed — `VerificationService.verify()` is a hardcoded stub unrelated to the schema | REVIEW-001 | `schemas/completion_contract.py`, `services/verification_service.py` | OPEN | |
| GAP-007 | HIGH | Most `ProjectProfile` security/git fields (`docker_socket`, `destructive_shell`, `spawn_subagents`, `network`, `force_push`, `signed_commits`) are schema-only and never enforced by `PolicyEngine` | REVIEW-001 | `schemas/project_profile.py`, `services/policy_engine.py` | OPEN | |
| GAP-008 | HIGH | Reconciliation (SPEC-03 §3.9, periodic Plane/Controller/opentasks divergence detection) has zero implementation and is not listed as a known/deferred gap anywhere | REVIEW-001 | *(no implementation found)* | OPEN | |
| GAP-009 | MEDIUM | Seven new dependencies (`asyncpg`, `pydantic-settings`, `httpx`, `structlog`, `python-json-logger`, `typer`, `aiosqlite`) added with no documented rationale, violating AGENTS.md's "do not add new dependencies without documenting why"; `structlog` + `python-json-logger` pairing especially unexplained | REVIEW-001 | `src/governance_controller/pyproject.toml` | OPEN | |
| GAP-010 | MEDIUM | `GET /executions/{id}` always returns 501 with a docstring claiming execution records aren't persisted yet — false, `Execution` rows are created by `ApprovalService._trigger_execution` | REVIEW-001 | `api/executions.py` | OPEN | |
| GAP-011 | MEDIUM | `docs/NEXT_STEPS.md` claims an "audit-log endpoint" exists; no such HTTP route exists anywhere — only the internal `AuditService`/`AuditLog` model | REVIEW-001 | `docs/NEXT_STEPS.md`, `api/` | OPEN | |
| GAP-012 | MEDIUM | Project Profile updates are silent/unaudited — `TaskService.create()` overwrites an existing profile with no `AuditService.log` call, despite profile changes altering policy enforcement for every task under that project | REVIEW-001 | `services/task_service.py` | OPEN | |
| GAP-013 | MEDIUM | `specs/SPEC-10-phase-plan.md` §10.1 acceptance checklist remains fully unchecked while `docs/NEXT_STEPS.md` declares Phase 1 "complete"; several specific criteria (dual-harness execution, non-stub git-cascade) look only partially satisfied by stub-level code | REVIEW-001 | `specs/SPEC-10-phase-plan.md`, `docs/NEXT_STEPS.md` | OPEN | |
| GAP-014 | MEDIUM | `EventBridge.handle()` silently swallows invalid-transition errors (`suppress(ValueError)`) and logs an audit entry indistinguishable from a successful transition, reducing audit fidelity | REVIEW-001 | `adapters/macro_agent/event_bridge.py` | OPEN | |
| GAP-015 | LOW | `test_placeholder.py` is vestigial scaffold (`assert True`), safe to delete now that real tests exist | REVIEW-001 | `tests/test_placeholder.py` | OPEN | |
| GAP-016 | LOW | `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/` has 23 of ~25 task brief/report files untracked (neither committed nor gitignored); `progress.md` is stale and references a nonexistent plan file | REVIEW-001 | `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/` | OPEN | |
| GAP-017 | LOW | `Settings` uses deprecated Pydantic v1-style `class Config:` instead of `model_config = SettingsConfigDict(...)` | REVIEW-001 | `governance_controller/config.py` | OPEN | |
| GAP-018 | LOW | Hardcoded local-dev DB credentials (`postgres:postgres`, `controller:controller`) with no comment marking them as dev-only defaults | REVIEW-001 | `governance_controller/config.py`, `docker-compose.yml` | OPEN | |
| GAP-019 | LOW | `api/__init__.py` re-exports `approvals`/`executions`/`tasks` but not `health`; cosmetic only, no functional bug | REVIEW-001 | `api/__init__.py` | OPEN | |
