# REVIEW-013: Full fresh review of the Phase 1 Governance Controller

Date: 2026-08-18
Scope: not a re-verification of prior findings — a full fresh read of the entire
`src/governance_controller/` codebase and its supporting docs/specs/requirements, on the assumption
that a subsystem no prior round specifically targeted could be hiding an undiscovered defect.
Three parallel read-only reviewers covered: (A) core governance services — a systematic
write-then-possibly-raise sweep of every DB-touching method, plus `PolicyEngine` composability,
models/schemas, and the API layer; (B) adapters/integrations/API surface, including the live
`POST /events` route; (C) tests/tooling/docs-sync/`requirements/REQUIREMENTS.md`/git hygiene/SDD
tracking. I independently re-read and confirmed the four most severe claims (Findings 1-4 below)
directly from source before writing this up.

## Baseline

`uv run pytest -q` → 175 passed. `uv run ruff check .` → clean. `uv run mypy governance_controller`
→ 0 errors. All three reviewers independently ran and confirmed this; no tooling regression since
REVIEW-012. **None of the findings below are tooling-detectable** — every one required live
reproduction or close manual reading; this is exactly why a full fresh review (rather than only
re-verifying named gaps) was worth doing after 12 rounds that had narrowed to specific files.

## Verdict: NOT clean — 1 new CRITICAL, 4 new HIGH, 4 new MEDIUM, 4 new LOW findings, plus one
## ledger correction (GAP-031 was stale-closed, not stale-open)

REVIEW-010/012's "zero open CRITICAL/HIGH" milestone no longer holds. `docs/NEXT_STEPS.md`,
`specs/SPEC-10-phase-plan.md`, and `requirements/REQUIREMENTS.md` have all been corrected this
round to reflect the findings below (see each file's diff / `reviews/GAPS.md` for exact wording).

### CRITICAL

**GAP-057 — Path-traversal bypasses forbidden-path enforcement in both `PolicyEngine` and
`VerificationService`.** `policy_engine.py::_is_inside`/`_normalize_path` (lines 94-106) and
`verification_service.py::_is_prefixed_by` (lines 207-211) both compare paths after only
`path.rstrip("/")` — I read both functions directly and confirmed neither calls
`os.path.normpath()` or `os.path.expanduser()` before the prefix/equality check. Concretely:
`contract.inputs=["src/../../srv/production/secrets.env"]` against
`profile.security.forbidden_paths=["/srv/production"]` returns `allowed=True, violations=[]` from
`PolicyEngine.evaluate()` — the reviewing agent live-reproduced this, and the code makes clear why
it must happen (there is simply no `..`-resolution step anywhere in the comparison). The identical
bypass reproduces in `VerificationService.verify_execution()`'s `forbidden_path_check`/`scope_check`.
This defeats the exact control `GAP-002` (also CRITICAL) was written to fix — GAP-002 fixed
exact-match-only comparison to prefix-match, but never addressed traversal normalization, in either
file. Fix shape: normalize both the forbidden-path list and every touched path through
`os.path.normpath()` (after `os.path.expanduser()` for `~`) before comparison, ideally via one
shared helper used by both files instead of two independent near-duplicates.

### HIGH

**GAP-058 — `VerificationService.verify_and_advance()`'s CAS-failure branch raises without
committing first.** `verification_service.py:236-252`: `AuditService.log(...)` then
`raise ValueError(...)`, with no `await db.commit()` between them — I confirmed this by reading the
block directly; it is the identical shape `GAP-044` fixed across three sibling branches in
`approval_service.py::_trigger_execution`, just never applied here. It matters because
`EventBridge.handle()` calls `verify_and_advance()` mid-request, after already performing its own
uncommitted `atomic_transition` earlier in the same call (`event_bridge.py:134-140`) — if the second
CAS loses a race, the whole request rolls back on `get_db()`'s `except BaseException`, erasing the
already-legitimate first transition and both audit rows, not just the verification failure's own
record. The reporting agent live-reproduced this with a seeded two-session race (0 audit rows
survived rollback). Fix: add `await db.commit()` before the `raise`, matching
`_trigger_execution`'s pattern, plus a regression test through the real `EventBridge.handle()` path
(not a unit test against `VerificationService` alone) analogous to `test_approval_concurrency.py`'s.

**GAP-059 — `PolicyEngine` never caps `TaskContract.execution.timeout_minutes`/`max_retries`
against `ProjectProfile.execution.timeout_minutes`.** I read `PolicyEngine.evaluate()` end-to-end
(`policy_engine.py:268-357`) and confirmed there is no comparison of these fields anywhere in the
method — `GAP-051` wired `timeout_minutes`/`max_retries` through to the outbound executor payload,
but nothing validates them against the project-level cap SPEC-03 §3.6 describes. Same
schema-only/unenforced-profile-constraint class as `GAP-007`, for fields GAP-007's fix didn't cover.

**GAP-060 — `POST /tasks` leaks an unhandled 500 on a duplicate `task_id`.** `api/tasks.py:25-36`'s
`create_task()` has no `try/except` around `TaskService.create()` — confirmed by reading the
handler; a duplicate `task_id` raises a raw `sqlalchemy.exc.IntegrityError` that propagates as an
unhandled 500 with a stack trace, instead of `409 Conflict`. Same pattern `GAP-005` fixed for
`POST /approvals`, never applied to `POST /tasks`. Live-reproduced by the reporting agent against
the real ASGI app; the transaction rollback itself is correct (no partial-data survives), so this is
purely an API-contract/status-code gap, not a data-integrity loss.

**GAP-061 — `uvicorn` is missing from `pyproject.toml`/`uv.lock`; the Docker container cannot
start.** `Dockerfile`'s `CMD ["uvicorn", ...]` depends on a package declared nowhere in
`dependencies`, `dev`, or any of `uv.lock`'s 39 locked packages. `docker-compose.yml`'s `api`
service builds from this same `Dockerfile`. The reporting agent live-reproduced this: `docker build`
succeeds, `docker run` fails with `exec: "uvicorn": executable file not found in $PATH`. This is the
one documented way to run the Controller in a container and has been completely broken through all
12 prior review rounds — no round built the actual image, and there is no CI workflow anywhere in
the repo that would have caught it.

### MEDIUM

- **GAP-062**: `POST /events` has no body-size limit and no rate limiting — combined with its
  already-known lack of authentication (noted under `GAP-047`, not re-litigated), a distinct
  resource-exhaustion exposure.
- **GAP-063**: `services/opentasks_service.py` is orphaned dead code (never called from `services/`,
  `api/`, or `adapters/`) — same pattern as the already-fixed `GAP-008`/`GAP-029`, honestly disclosed
  as a stub in `docs/NEXT_STEPS.md` so not itself a doc-accuracy violation, but the same "wire it in
  or remove it" decision those two forced is still outstanding here.
- **GAP-064**: residual from `GAP-031` (see ledger correction below) — `api/approvals.py` only
  catches `ValueError`; a `RuntimeError` from macro-agent start failure leaks as an unhandled 500
  instead of a mapped status code. `GAP-044` fixed the data-loss half of this path; the
  status-code half was never addressed.
- **GAP-065**: `requirements/REQUIREMENTS.md` had drifted in four ways — `RISK-14` said `GAP-024`
  was "not yet mitigated" though it's been `CLOSED` since REVIEW-005; `IFR-05` documented a
  `/webhooks/macro-agent` path that was never built (the real route is `/events`); `IFR-02`
  documented a nonexistent `POST /tasks/{id}/execute`; and no `RISK` row captured the recurring
  async-session-lifecycle defect class (9 of the now-71 gaps, 9 of 12 review rounds before this one).
  **Corrected directly in this review**: `RISK-14` updated, `IFR-02`/`IFR-05` corrected to describe
  the real interfaces, new `RISK-16` added.

### LOW

- **GAP-066**: `docker-compose.yml`'s `GC_LOG_LEVEL: info` is dead configuration — `Settings` has no
  `log_level` field, `structlog` is never `.configure()`'d.
- **GAP-067**: `EventBridge._EVENT_TO_STATE` implements only 6 of SPEC-05 §5.4's 9 event types —
  `stream:committed`, `mergeQueue:added`, `mergeQueue:ready` are silent no-ops, undisclosed anywhere.
- **GAP-068** (**closed directly this review**): `SPEC-10`'s `[^phase1-git-cascade]` footnote still
  described a landing stub deleted by `0920b65` when `GAP-029`/`GAP-030` closed. Corrected.
- **GAP-069** (**closed directly this review**): `docs/NEXT_STEPS.md`'s endpoint bullet omitted
  `POST /events`. Corrected.
- **GAP-070** (**accepted, not actionable**): commit `8926506`'s message doesn't match its diff
  (mentions a file, `scheduled_tasks.lock`, that never existed in this repo). Benign content;
  rewriting a published commit message is out of proportion — noted for the record only.
- **GAP-071**: `.superpowers/sdd/.../progress.md` has gone stale again exactly as `GAP-016`
  originally flagged — stops at Review 1/2, no entry for Review 3 through this one. Purely an
  SDD-tracking artifact; needs a deliberate backfill pass, not attempted here to avoid a rushed,
  possibly-inaccurate edit.

## Ledger correction: `GAP-031` was marked `IN_PROGRESS` but is actually fixed

Two of the three parallel reviewers (B and C), working independently, both noticed
`reviews/GAPS.md`'s `GAP-031` row was still `IN_PROGRESS` with no `Closed By`. I checked what it
actually describes: `executor.start()` failure raising `RuntimeError` uncaught, rolling back the
*entire transaction* and erasing the Execution row / FAILED marking / audit entries. That specific
defect is fixed — `GAP-044`'s commit-before-raise fix (`3e93928`) makes exactly this survive
rollback, independently reproduced in REVIEW-010 and re-confirmed by all three reviewers this round.
The ledger status was simply never updated after the fix landed; `docs/NEXT_STEPS.md`'s past claims
of "zero open gaps" were therefore imprecise even before this round's new findings. **Corrected to
`CLOSED` in this review.** The narrower residual this row's text did *not* originally describe — the
uncaught `RuntimeError` still surfacing as a bare 500 — is real, but is a distinct gap, now tracked
separately as `GAP-064`.

## Item-by-item sweep results (no new finding)

- **Write-then-possibly-raise sweep**: of 22 identified DB-writing branches across
  `approval_service.py`, `audit_service.py`, `task_service.py`, `state_machine.py`,
  `verification_service.py`, and `event_bridge.py`, 20 are safe (commit before raise, or no raise on
  that path); only the 2 both traced to `GAP-058` are not.
- **`PolicyEngine` composability**: every check category unconditionally appends to a flat
  `violations` list with no early return — no combination of present/absent fields causes a whole
  category to be silently skipped. (The category-3 forbidden-path check is still defeatable, but via
  `GAP-057`'s traversal bug, not a composability/ordering issue.)
- **`StateMachine.transition()` vs `atomic_transition()`**: confirmed (again) that no production
  code path calls the old mutating `transition()` — all live state changes go through
  `atomic_transition()` (CAS) or `validate_transition()` (pure check), consistent with `GAP-038`.
- **Models/schemas**: Pydantic v2's mutable-default deep-copy behavior confirmed correct (not a bug)
  for `task_contract.py`/`project_profile.py`/`completion_contract.py`'s list/dict/nested-model
  defaults. `Task.version`'s `server_default` (GAP-052) and `ProcessedEvent`'s composite unique
  index (GAP-046/026) both still intact. No `ForeignKey` from `Approval`/`AuditLog`/`Execution`/
  `ProcessedEvent`'s `task_id` to `Task.id` — noted as a deliberate tradeoff (the `"unknown"`
  sentinel `task_id` `event_bridge.py` uses for untraceable events would violate a real FK), not a
  blind gap.
- **`test_placeholder.py`/dependency documentation/README layout table/SPEC-10 checked-item
  balance**: no new issues; all consistent with prior rounds.
- **Test suite characterization**: 175 tests across 25 files, spot-checked 4 files not named in
  `GAPS.md` (`test_macro_agent_executor.py`, `test_opentasks_service.py`, `test_harness_registry.py`,
  `test_config.py`) — all substantive, no padding.

## Verification

`cd src/governance_controller && uv run pytest -q` → 175 passed (unchanged; no application or test
code was modified by this review — all findings are read-only observations, all corrections in this
round were to documentation/requirements/ledger files only). `uv run ruff check .` → clean.
`uv run mypy governance_controller` → 0 errors. `GAP-057` and `GAP-058` were independently
re-confirmed by direct source reading in this session (not just taken from the reporting agents'
claims); `GAP-060`/`GAP-061` were confirmed by reading the exact cited code/`Dockerfile`/`uv.lock`
state.

## Files corrected this round (documentation/ledger only, no application code touched)

- `reviews/GAPS.md` — added `GAP-057` through `GAP-071`; corrected `GAP-031`'s status to `CLOSED`.
- `docs/NEXT_STEPS.md` — replaced the "no open gaps" claim with the current CRITICAL/HIGH list;
  added `POST /events` to the endpoint bullet.
- `specs/SPEC-10-phase-plan.md` — corrected the stale git-cascade footnote; added footnotes to the
  "policy check" and verification/durability items flagging `GAP-057`/`GAP-058`.
- `requirements/REQUIREMENTS.md` — corrected `RISK-14`, `IFR-02`, `IFR-05`; added `RISK-16`.
