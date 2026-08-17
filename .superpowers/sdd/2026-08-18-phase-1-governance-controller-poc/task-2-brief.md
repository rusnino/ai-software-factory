# Task 2 Brief: Configuration and Database Setup

**Goal:** Add Pydantic Settings, async PostgreSQL engine/session setup, SQLModel base, and test fixtures for the Governance Controller.

**Files to create:**
- `src/governance_controller/governance_controller/config.py`
- `src/governance_controller/governance_controller/db.py`
- `src/governance_controller/tests/conftest.py`
- `src/governance_controller/governance_controller/models/__init__.py`

**Files to modify:**
- None (this task creates new files only).

**Exact values to use verbatim:**
- `Settings.database_url` default: `postgresql+asyncpg://postgres:postgres@localhost/governance`
- Environment prefix: `GC_`
- Test database URL: `postgresql+asyncpg://postgres:postgres@localhost/governance_test`
- `echo=False` and `future=True` for engine.
- `expire_on_commit=False` for `AsyncSessionLocal`.
- `async_mode = "auto"` is already in pyproject.toml pytest settings.

**Interfaces produced:**
- `Settings` class from `governance_controller.config`
- `engine`, `AsyncSessionLocal`, `init_db()`, `get_db()` from `governance_controller.db`
- `Base` class from `governance_controller.models` (SQLModel-based)
- `db_session` pytest fixture from `tests/conftest.py`

**Constraints:**
- Use `uv` / `uvx`.
- No runtime dependency on Plane or Macro UI.
- Async SQLAlchemy + SQLModel.
- Test DB isolated from development DB.

**Verification steps:**
1. Run `uv run pytest tests/test_placeholder.py -v` to ensure pytest works.
2. Add a test `tests/test_config.py` that imports Settings and asserts default database_url.
3. If PostgreSQL is running locally, run a test that calls `init_db()` in a transaction and rolls back.
4. If PostgreSQL is NOT running locally, the test should be skipped gracefully (use pytest skip).

**Commit message:** `feat: add settings and async PostgreSQL/SQLModel setup`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-2-report.md`
