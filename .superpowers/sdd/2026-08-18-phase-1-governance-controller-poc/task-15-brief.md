# Task 15 Brief: Health Check Endpoint

**Goal:** Add a `GET /health` endpoint that reports API and database connectivity status for orchestrators and load balancers.

**Files to create:**
- `src/governance_controller/governance_controller/api/health.py`
- `src/governance_controller/tests/test_health_endpoint.py`

**Files to modify:**
- `src/governance_controller/governance_controller/main.py` (include health router)

**Exact values to use verbatim:**

Response schema:
```python
class HealthResponse(BaseModel):
    status: str  # "ok" or "degraded"
    database: str  # "connected" or "disconnected"
    version: str = "0.1.0"
```

Endpoint: `GET /health`

Behavior:
- Query DB with `select 1`.
- If successful, return `{"status": "ok", "database": "connected", "version": "0.1.0"}`.
- On exception, return `{"status": "degraded", "database": "disconnected", "version": "0.1.0"}` with HTTP 503.

**Interfaces produced:**
- `GET /health` endpoint in `governance_controller.api.health`

**Interfaces consumed:**
- `get_db` from `governance_controller.db`

**Constraints:**
- Must not require authentication.
- Keep DB check lightweight.

**Verification steps:**
1. Write `tests/test_health_endpoint.py` covering:
   - healthy DB returns 200 and ok.
   - DB failure returns 503 and degraded.
2. Run `uv run pytest tests/test_health_endpoint.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add GET /health endpoint with DB connectivity check`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-15-report.md`
