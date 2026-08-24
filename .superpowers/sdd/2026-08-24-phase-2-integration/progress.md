# Phase 2 Integration Progress

Started: 2026-08-24

## Local Environment

- Plane CE: `http://127.0.0.1:8081`
  - workspace: `ai-factory`
  - project_id: `4e8e52d0-1779-41e7-8c67-d256d48b1654`
  - api_token: `dev-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
- macro-agent service: not installed yet (Task 4)

## Completed Tasks

- **Task 1** (commit `7c0bd5c`): Real Plane CE HTTP client
  - Added `PlaneClient` with endpoints for projects, issues, comments, dependencies, states.
  - Added `GC_PLANE_BASE_URL`, `GC_PLANE_API_TOKEN`, `GC_PLANE_WORKSPACE_SLUG`, `GC_PLANE_PROJECT_ID` config.
  - Added unit tests with httpx mocks and contract tests against local Plane CE.
  - Verified: 298 passed (SQLite), 301 passed (PostgreSQL), ruff + mypy clean.

- **Task 2** (commit `c1b6648`): Plane webhook receiver
  - Added `POST /webhooks/plane` that translates eligible Plane state changes into Controller approvals.
  - Enforced human actor, single_update, known state maps, stale-state guard, and best-effort Plane revert on rejection.
  - Added isolated-DB tests and fixed `test_db.py` module-reload leakage that broke later suites.
  - Verified: 306 passed (SQLite), 309 passed (PostgreSQL), ruff + mypy clean.

- **Task 3** (commit `ddc7671`): Controller → Plane projection service
  - Added `PlaneProjectionService` with issue create, state update, and comment helpers.
  - Runtime Plane state UUID resolution by display name.
  - No-op when Plane is not configured; unit-tested with fake client.
  - Verified: 311 passed (SQLite), 314 passed (PostgreSQL), ruff + mypy clean.

## Open Tasks

- Task 4: macro-agent service scaffold
- Task 3: Controller → Plane projection service
- Task 4: macro-agent service scaffold
- Task 5: Real macro-agent executor client
- Task 6: opentasks runtime DAG materializer
- Task 7: Reconciliation job
- Task 8: Intake adapter (Telegram + Email)
- Task 9: Verification failure feedback + terminal alerting
- Task 10: Optional OPA backend client

## Blockers

None.
