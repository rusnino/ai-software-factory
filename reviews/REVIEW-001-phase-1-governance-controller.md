# REVIEW-001: Phase 1 Governance Controller implementation

Date: 2026-08-18
Scope reviewed: `src/governance_controller/` as of commit `1bca0ef` (22 feature commits, single ~9-hour
session, author `rufo0618`).
Reviewed against: `specs/SPEC-03-governance.md`, `decisions/ADR-001-governance-controller-implementation.md`,
`AGENTS.md`, `specs/SPEC-10-phase-plan.md` §10.1.
Tracking: every finding below has a corresponding row in `reviews/GAPS.md` (`GAP-001` .. `GAP-019`).

## Summary

Substantial, well-structured work: ~4,100 lines of production + test code, 116 passing tests, ruff-clean,
good commit hygiene (small, conventionally-prefixed, tests-with-feature commits). However, two findings are
direct violations of `AGENTS.md`'s non-negotiable constraints, and several more are conformance gaps against
`SPEC-03`. `docs/NEXT_STEPS.md`'s "complete and fully tested" framing for Phase 1 is premature until the
`CRITICAL`/`HIGH` gaps below are closed (see `AGENTS.md` § Review Findings and Gap Tracking for the gate rule).

**Test suite note:** the suite was actually executed (`uv run pytest -q` → 116 passed; `uv run ruff check .` →
clean) rather than inferred from a stale cache. Caveat: no Postgres was running during that run, so all 116
tests exercised the SQLite fallback path in `tests/conftest.py`, not the `asyncpg`/PostgreSQL path that
`CLAUDE.md` mandates for production.

## Findings

### CRITICAL — violates AGENTS.md non-negotiable constraints

**GAP-001 — Self-approval prevention is unenforced.** `governance_controller/services/permission_service.py`
implements the right logic (rejects `"system"`/`"agent"` actors, requires admin for execution/merge), but it
is never imported or called by `ApprovalService` (`services/approval_service.py`) or the `POST /approvals`
handler (`api/approvals.py`). `ApprovalRequest.actor` is a bare `str` with no validation, and neither `Task`
nor `TaskContract` records who *proposed* the task — so even wiring `PermissionService` in today couldn't
detect "the same identity approved its own work," because there's no "proposer" field to compare against.
`AGENTS.md` lists "Do not allow agents to approve their own work" as non-negotiable and not phase-gated;
`docs/NEXT_STEPS.md` defers the fix to Phase 2 without invoking `AGENTS.md`'s own Phase 1 Stop Condition
mechanism, while simultaneously declaring Phase 1 complete.

**GAP-002 — PolicyEngine's forbidden-path check has inverted logic.** `services/policy_engine.py:56-60`:
```python
contract_forbidden = set(contract.forbidden_paths)
profile_forbidden = set(profile.security.forbidden_paths)
conflicts = sorted(contract_forbidden & profile_forbidden)
for path in conflicts:
    violations.append(f"Forbidden path conflict: {path}")
```
This rejects an approval whenever the Task Contract and Project Profile *agree* on a forbidden path (set
intersection), instead of checking whether the task's inputs/deliverables touch a forbidden path. Concrete
failure: `SPEC-03`'s own canonical example YAMLs (§3.5, §3.6) both list `~/.ssh` and `/srv/production` as
forbidden paths — copying those examples verbatim makes every approval for that task fail policy with
`"Forbidden path conflict: ~/.ssh"`.

### HIGH — spec conformance gaps

**GAP-003 — `Idempotency-Key` header doesn't exist.** `POST /approvals` (`api/approvals.py:41-46`) never
declares a header parameter. It unconditionally computes a synthetic key from
`(task_id, approval_type, actor, timestamp)`, which SPEC-03 §3.3 specifies only as a *fallback* for when the
header is absent — the primary mechanism is simply missing.

**GAP-004 — The idempotency key is functionally dead.** `services/approval_service.py:99-105` does
idempotency lookup via `(task_id, approval_type, actor)` only; `idempotency_key` is threaded through, stored
on the `Approval` row, and never read back. Two genuinely distinct approval attempts (different timestamps,
different real idempotency keys) from the same actor/type silently collapse into a no-op.

**GAP-005 — Status codes don't match SPEC-03 §3.3.** Policy violations return `422`
(`api/approvals.py:109-112`) where the spec requires `403`; `400` is never emitted anywhere. A malformed
`timestamp` string passes Pydantic (typed as plain `str`) then hits an unguarded `datetime.fromisoformat()`
in `_make_idempotency_key` (line 28), called *before* the try/except around `approve()` — this raises an
uncaught `ValueError` → unhandled HTTP 500, not the documented 400.

**GAP-006 — Completion Contract schema is unused.** `schemas/completion_contract.py` matches SPEC-03 §3.7
structurally, but `VerificationService.verify()` is a hardcoded stub that only pattern-matches literal
`"__pycache__"`/`".env"` strings — it never references the `CompletionContract` model.

**GAP-007 — Most Project Profile security/git fields are schema-only.** `docker_socket`,
`destructive_shell`, `spawn_subagents`, `network`, `force_push`, `signed_commits` are defined in
`schemas/project_profile.py` but never read by `PolicyEngine` or anywhere else — only `allowed_harnesses`,
the (buggy) `forbidden_paths`, and `merge_requires_human` are actually enforced.

**GAP-008 — Reconciliation (SPEC-03 §3.9) has zero implementation** — no scheduler, no job — and isn't
listed as a known/deferred gap in `docs/NEXT_STEPS.md` the way other stubs are. Reasonable to defer given no
live Plane integration exists yet, but should be named as a gap rather than silently absent.

### MEDIUM

**GAP-009 — Undocumented new dependencies:** `asyncpg`, `pydantic-settings`, `httpx`, `structlog`,
`python-json-logger`, `typer`, `aiosqlite` — all added in the initial scaffold commit with no comment/ADR
rationale, violating `AGENTS.md`/`CLAUDE.md`'s "do not add new dependencies without documenting why." The
`structlog` + `python-json-logger` pairing (two structured-logging libraries) is particularly unexplained.

**GAP-010 — `GET /executions/{id}` always returns 501** with a docstring claiming "Execution records are not
persisted yet" — false; `Execution` rows are created in `ApprovalService._trigger_execution`.

**GAP-011 — `docs/NEXT_STEPS.md` claims an "audit-log endpoint" exists** — it doesn't; only the internal
`AuditService`/`AuditLog` model exist, no HTTP surface.

**GAP-012 — Project Profile updates are silent/unaudited.** `TaskService.create()` overwrites an existing
`ProjectProfileModel` with no audit log entry, despite a profile change altering policy enforcement for every
task under that project.

**GAP-013 — SPEC-10 §10.1's acceptance checklist is still fully unchecked** while `docs/NEXT_STEPS.md`
declares Phase 1 done. A few specific criteria (dual-harness execution, non-stub git-cascade) look only
partially satisfied by stub-level code.

**GAP-014 — `EventBridge.handle()` silently swallows invalid-transition errors** (`with suppress(ValueError)`)
and still logs an audit entry indistinguishable from a successful transition.

### LOW / hygiene

**GAP-015** — `test_placeholder.py` is vestigial `assert True` scaffold, safe to delete.

**GAP-016** — `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/`: 23 of ~25 task brief/report
files are untracked (neither committed nor gitignored); `progress.md` is stale (checks off only tasks 1-2
despite 20 tasks' worth of commits) and references a plan file that doesn't exist anywhere in the repo.

**GAP-017** — `config.py` uses deprecated Pydantic v1-style `class Config:` — already self-acknowledged in
`docs/NEXT_STEPS.md` as the one warning.

**GAP-018** — Hardcoded local-dev DB credentials (`postgres:postgres`, `controller:controller`) with no
comment marking them dev-only.

**GAP-019** — `api/__init__.py` doesn't re-export `health` (cosmetic only).

## Recommendation

`GAP-001` and `GAP-002` must close before Phase 1 can honestly be called done — they are direct violations
of `AGENTS.md`'s non-negotiable rules, not stylistic issues. `GAP-003`..`GAP-008` are real conformance gaps
against `SPEC-03` that a reviewer or future integration would hit quickly. `docs/NEXT_STEPS.md`'s framing
should reflect this until at least the `CRITICAL` items are closed.

## Verification

`cd src/governance_controller && uv run pytest -q` (116 passed as of this review) and `uv run ruff check .`
(clean). All `CRITICAL`/`HIGH` findings were independently confirmed by reading the cited source directly,
not taken secondhand.
