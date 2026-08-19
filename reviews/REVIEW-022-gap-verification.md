# REVIEW-022: Verification of GAP-094's fix — ledger reaches zero open rows

Date: 2026-08-19
Scope: live re-verification of `GAP-094` (commit `a04e231`), the last `OPEN` row in `reviews/GAPS.md`
after REVIEW-021.

## Fix verified: GENUINELY FIXED, no regression

`db.py` now builds `_ENGINE_KWARGS` conditionally: `pool_pre_ping` always, but `pool_size`/
`max_overflow`/`pool_timeout` only when `not settings.database_url.startswith("sqlite")`.

Live-reproduced:
- `GC_DATABASE_URL=sqlite+aiosqlite:///:memory: uv run python3 -c "import governance_controller.db"`
  now succeeds (previously raised `TypeError`). `engine.pool` is a real `StaticPool`.
- Full end-to-end check against SQLite: created and committed a `Task` row through
  `AsyncSessionLocal`/`ensure_sqlite_tables()` — succeeds.
- No regression to the original `GAP-083`/`GAP-091` fix: with the default (Postgres) `database_url`,
  the engine still receives real pool kwargs (`AsyncAdaptedQueuePool`, `pool.size() == 10`).
- `uv run pytest -q` → 212 passed. `ruff check .` → clean. `mypy governance_controller` → 0 errors.
- `git merge-base --is-ancestor a04e231 HEAD` confirms the hash cited in `GAPS.md` (corrected by the
  coding agent's own follow-up commit `3ec4fa0` this time, without needing a reviewer correction) is a
  genuine ancestor.

## Milestone

`reviews/GAPS.md` now has **zero `OPEN` or `IN_PROGRESS` rows** — every one of the 94 tracked gaps is
`CLOSED` or `ACCEPTED`. This is the first time in this 22-review series that a "gate clean" state has
been reached *and* survived the very next round's independent re-check with nothing new found in the
process (every prior clean milestone — REVIEW-010/012, REVIEW-014/015, REVIEW-017, and the multi-round
convergence from REVIEW-019 through REVIEW-021 — was reopened by the next fresh-eyes pass, sometimes
within the same review that verified the previous fix).

This should not be read as "Phase 1 is done" — `docs/NEXT_STEPS.md`'s Phase 2 candidate-task list
still names substantial, honestly-disclosed deferred work (real Plane/macro-agent integration,
reconciliation, Meta Orchestrator, Intake Adapter, security/auth hardening, a real outbound alert
channel for SPEC-09 §9.6, CI enforcement of the dual-DB test path). It means the specific defects this
review series has been hunting for are, as of this round, exhausted.

## Verification

Personally performed; no application or test code was modified. `docs/NEXT_STEPS.md` updated to
reflect the milestone.
