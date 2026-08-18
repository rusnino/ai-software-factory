# Task 15 Report: Health Check Endpoint

## Status

Completed.

## Commits

- `8fba214` — `feat: add GET /health endpoint with DB connectivity check`

## Test Summary

- `uv run pytest tests/test_health_endpoint.py -v`: **2 passed**
  - `test_health_returns_ok_when_db_is_connected` — returns 200 with `{"status": "ok", "database": "connected", "version": "0.1.0"}`
  - `test_health_returns_degraded_when_db_is_disconnected` — returns 503 with `{"status": "degraded", "database": "disconnected", "version": "0.1.0"}`
- `uv run pytest` (full suite): **83 passed, 1 warning**
  - The single warning is an existing Pydantic `class-based config` deprecation in `governance_controller/config.py`, unrelated to this change.
- `uv run ruff check governance_controller tests`: **All checks passed**

## Files Changed

- Created `src/governance_controller/governance_controller/api/health.py`
- Created `src/governance_controller/tests/test_health_endpoint.py`
- Modified `src/governance_controller/governance_controller/main.py` (includes the new health router)

## Concerns

None. Endpoint follows the brief verbatim; no authentication is enforced, and the DB check uses a lightweight `select 1`.
