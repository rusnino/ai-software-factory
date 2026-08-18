# REVIEW-016: Full fresh review of the Phase 1 Governance Controller

Date: 2026-08-18
Scope: not a re-verification of prior findings — a full fresh read of the entire
`src/governance_controller/` codebase, its supporting specs/docs/requirements, and (new this round)
its actual behavior against real PostgreSQL, on the theory that 15 prior rounds spent on named files
and re-verification could still be missing something systemic no one had looked for. Three parallel
reviewers covered: (A) core governance services, models, and concurrency, including — critically —
spinning up a real Postgres container, since every prior round's test suite and live reproduction ran
exclusively against SQLite; (B) adapters/API/security surface (Telegram, Plane adapter, CLI, harness
registry, all API routes); (C) full SPEC-01-through-SPEC-10 conformance, the SPEC-10 §10.1 checklist,
tests, dependencies, and README. I personally re-verified the most severe finding from each of the
three reports (the CRITICAL Postgres defect, the HIGH `forbidden_paths` bypass, and a ledger-hygiene
error I had made myself) directly before writing this up.

## Baseline

`uv run pytest -q` → 186 passed. `uv run ruff check .` → clean. `uv run mypy governance_controller` →
0 errors. No tooling regression since REVIEW-015. **The most severe finding this round is not
tooling-detectable and never has been** — it only surfaces when the Controller is actually pointed at
PostgreSQL, which no prior round had done end-to-end.

## Verdict: NOT clean — 1 new CRITICAL, 3 new HIGH, 3 new MEDIUM findings, plus one of my own
## ledger-hygiene mistakes from REVIEW-014 caught and corrected

The `CRITICAL`/`HIGH` gate is reopened for the third time in this series (after REVIEW-013 and now
this round). All four are genuinely new — none duplicate anything in `reviews/GAPS.md`'s prior 72
rows.

### CRITICAL

**GAP-078 — The Controller has never actually worked against real PostgreSQL.** Every `datetime`
column across all 5 models (`Task.created_at`/`updated_at`, `Approval.timestamp`,
`AuditLog.timestamp`, `Execution.started_at`/`ended_at`, `ProcessedEvent.event_timestamp`/
`processed_at`) is declared as a plain `datetime` field with no timezone-aware column type override,
so SQLModel generates `TIMESTAMP WITHOUT TIME ZONE` — I confirmed this directly:
`Task.__table__.columns['created_at'].type.timezone == False`. Meanwhile every `utc_now()`/
`datetime.now(UTC)` call throughout the services layer produces a timezone-*aware* Python datetime.
`asyncpg` (the project's actual configured driver) rejects binding a tz-aware value into a tz-naive
column outright — SQLite, which every one of the 186 checked-in tests and every prior round's live
reproduction used, silently tolerates this mismatch, which is exactly why it survived 15 rounds
undetected. I independently reproduced this myself: spun up a `postgres:16-alpine` container, pointed
the real `governance_controller.db` engine at it, and called `TaskService.create()` — the simplest
possible write in the whole system — through the real `get_db()` lifecycle:
```
FAILED: DBAPIError ... asyncpg.exceptions.DataError: invalid input for query argument $7:
  datetime.datetime(2026, 8, 18, 17, 13, 9, ...) (can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO auditlog (...) ...]
```
Since every write path shares these same model classes, this is not a narrow bug — it means task
creation, approvals, executions, verification, and the event bridge are all completely non-functional
against the database `CLAUDE.md`, `AGENTS.md`, and `docker-compose.yml` all specify as the Phase 1
target. Fix shape: add `sa_column=Column(DateTime(timezone=True), ...)` to every datetime field
across all 5 models (preferable, since the code's own semantics assume tz-aware UTC throughout), or
make every `utc_now()`/`datetime.now(UTC)` call produce a naive UTC value consistently instead.

### HIGH

**GAP-079 — `TaskService.create()`'s project-profile upsert is a TOCTOU race that silently drops a
task and mislabels the failure.** `task_service.py:36-58` does `select(...).where(project_id==...)`
then `db.add(...)` if `None`, with no locking or `ON CONFLICT`. Live-reproduced by the reporting
agent against the real ASGI app: two concurrent `POST /tasks` for different `task_id`s sharing a
brand-new `project_id` race on the profile check; the loser's request fails with `IntegrityError` on
the *profile* insert, but `api/tasks.py`'s generic `except IntegrityError` handler (the `GAP-060` fix)
reports `"Task with id {task-B} already exists"` — false, since `task-B` never existed and is never
created. The caller is told the wrong thing in a way that plausibly suppresses a needed retry.

**GAP-080 — `get_by_id_for_update()`'s row lock is held across the live macro-agent HTTP call,
contradicting the codebase's own fail-fast CAS design.** `api/approvals.py:63` acquires a `SELECT ...
FOR UPDATE` lock at the top of every `POST /approvals` request; for `EXECUTION` approvals, that lock
is held through `_trigger_execution`'s live outbound call to macro-agent. On SQLite (every prior
round's environment) this is a documented no-op (`GAP-023`'s own notes), which is why 15 rounds never
surfaced it. Live-reproduced by the reporting agent against real Postgres: a session simulating a 4s
slow macro-agent call blocks a second session's identical lock acquisition for 3.5s — directly
contradicting the fail-fast design `atomic_transition()`'s CAS pattern is built around elsewhere in
this same codebase. No `lock_timeout`/`statement_timeout` is configured anywhere, so a genuinely hung
macro-agent (the exact scenario `GAP-064`'s `RuntimeError→503` handling anticipates as real) would
wedge that task's lock indefinitely, with retrying callers' connections pinned from the pool — a
latent path to pool exhaustion.

**GAP-074 — `TaskContract.forbidden_paths` (SPEC-03 §3.5) is never enforced pre-approval, and only
conditionally post-execution.** I personally re-verified this: `PolicyEngine.evaluate()` never reads
`contract.forbidden_paths` anywhere (confirmed by grep and by reading the full method) — only
`profile.security.forbidden_paths`. Live-reproduced myself:
```
contract.inputs=['/srv/production/db.sql'], contract.forbidden_paths=['/srv/production']
→ PolicyEngine.evaluate(): allowed=True, violations=[]
```
`VerificationService.verify_execution()` only checks `contract.forbidden_paths` in its `else` branch —
i.e. only when no `CompletionContract` is attached, which is precisely the opposite of SPEC-03's own
canonical examples that pair the two together. The one test that appears to guard this
(`test_forbidden_path_agreement_is_allowed`) never sets `inputs`/`deliverables`, so it passes
trivially regardless of whether enforcement exists. Same defect class as the already-fixed
`GAP-002`/`GAP-007`/`GAP-057` (a declared safety control silently unenforced). Mitigating factor:
`profile.security.forbidden_paths` remains correctly enforced and traversal-safe (`GAP-057`) — this is
specifically the task-level, proposer-declared narrowing that's dead, not a full bypass.

### MEDIUM

- **GAP-073**: `GAP-062`'s body-size limit middleware only applies to `POST /events` — `POST /tasks`
  and `POST /approvals` share the identical unauthenticated, unbounded-body exposure and have no cap
  at any layer. Live-reproduced: a 5 MB body to `/events` correctly returns `413`; the same body to
  `/tasks`/`/approvals` is fully parsed and reaches pydantic validation / the DB layer.
- **GAP-076**: `docs/NEXT_STEPS.md` misassigned "Meta Orchestrator integration" and "Intake Adapter"
  to "Deferred to Phase 3+" when `SPEC-10-phase-plan.md` §10.2 explicitly assigns both to Phase 2.
  **Corrected directly this round** — moved into the Phase 2 candidate-task list.
- **GAP-077**: SPEC-09 §9.6's retry-before-`FAILED` mechanism (with macro-agent failure feedback) is
  entirely unimplemented and, unlike every other Phase 1 deferral, was undisclosed anywhere. Fails
  closed (never a false `DONE`), so not a governance defect — just a silently missing SPEC-required
  feature. **Disclosure added directly this round** (`docs/NEXT_STEPS.md` Phase 2 candidate task #10);
  the mechanism itself remains unimplemented, so the row stays open as a real feature gap.
- **GAP-075**: `SPEC-10-phase-plan.md`'s footnotes for `GAP-057`/`GAP-058` still said `OPEN` (one
  claimed `GAP-057` "currently blocks declaring Phase 1 acceptance criteria met") though both were
  independently live-verified `CLOSED` in REVIEW-014/015 — the file was never touched after the
  commit that *opened* them. **Corrected directly this round.**

## Ledger-hygiene correction: my own oversight from REVIEW-014

One of the three reviewers independently noticed that `reviews/GAPS.md` still listed `GAP-058` and
`GAP-060` as `OPEN` with no `Closed By`, even though `docs/NEXT_STEPS.md` (also mine) asserted
blanket completeness on top of that stale ledger — the same class of defect as the already-closed
`GAP-027` ("docs claim complete while the ledger it cites says otherwise"), except this time it was my
own bookkeeping gap, not the neighboring agent's. I had verified both fixes as genuinely correct in
REVIEW-014's prose but never flipped their ledger rows from `OPEN` to `CLOSED`. **Corrected directly
this round** — both rows now show `CLOSED` with their actual fix commits (`d7ed2a4`, `715a50b`).

## Item-by-item sweep results (no new finding)

- **Telegram adapter, Plane adapter, CLI, harness registry**: all re-read fresh; no regression, no
  new issue. The harness registry's mutable-singleton `.register()` is theoretically abusable but has
  no HTTP-reachable call site — exploiting it requires code execution in-process already, a strictly
  larger compromise, so not flagged as a standalone gap.
- **API response schemas**: no accidental internal-field leaks in any response model.
- **Dependencies**: `docs/DEPENDENCIES.md`/`pyproject.toml`/`uv.lock` three-way cross-check clean,
  including `uvicorn`'s (`GAP-061`) documented rationale.
- **README.md**: read fully fresh, no stale claims found.
- **SPEC-01, SPEC-02, SPEC-04, SPEC-06, SPEC-08**: all consistent with current implementation and
  prior disclosures; SPEC-06 §6.2 role lists re-confirmed exact.
- **Self-approval prevention (`GAP-001`)**: re-confirmed intact and independent of
  `PermissionService`'s role-only logic.
- **Test spot-checks**: `test_cli.py`, `test_plane_adapter.py`, `test_permission_service.py`,
  `test_state_machine.py` all substantive, no padding.

## Verification

`GAP-078` (CRITICAL) and `GAP-074` (HIGH) were personally re-verified by live reproduction — the
former against a real `postgres:16-alpine` Docker container I started and removed myself, the latter
against the real `PolicyEngine`/`VerificationService` code. The ledger-hygiene correction was
personally confirmed by re-reading the exact rows and cross-referencing REVIEW-014's own text.
`GAP-079`/`GAP-080`/`GAP-073` were confirmed via the reporting agents' detailed, code-and-output-cited
live reproductions, consistent with this series' established rigor; not independently re-run given
time constraints, but each report included exact reproduction steps and output, not just a diff read.
No application or test code was modified by this review — all changes in this round are to
documentation/ledger files: `reviews/GAPS.md`, `docs/NEXT_STEPS.md`, `specs/SPEC-10-phase-plan.md`.

## Note on a concurrency hazard during this round

One reviewing agent observed live, uncommitted edits appearing on disk mid-review and correctly
inferred another process might be editing the same files concurrently — this was actually me,
compiling findings from the other two agents' reports while this one was still running, not a second
independent review process. No collision occurred (the agent correctly avoided touching the files it
saw changing), but it's a reminder that running fresh-review agents in parallel with live
documentation edits in the same working tree needs care.
