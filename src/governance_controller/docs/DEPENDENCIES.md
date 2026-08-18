# Dependency Rationale

This document records why each non-stdlib runtime dependency was added to the
Governance Controller. It satisfies the project convention that new dependencies
must be documented.

## Runtime dependencies

| Dependency | Why it is needed |
|---|---|
| `fastapi` | HTTP API framework for `/tasks`, `/approvals`, `/executions`, `/health`, and `/audit-log`. |
| `pydantic` | Data validation and serialization for request/response schemas and settings. |
| `pydantic-settings` | `BaseSettings` integration for environment-variable based configuration. |
| `sqlmodel` | ORM/model layer unifying Pydantic and SQLAlchemy for `Task`, `Execution`, `Approval`, `AuditLog`. |
| `asyncpg` | Async PostgreSQL driver used by SQLAlchemy in production. |
| `httpx` | Async HTTP client for macro-agent executor and Telegram/Plane adapters. |
| `structlog` | Structured, typed logging; renders the audit trail and application logs. |
| `typer` | CLI framework for the `governance-controller` command-line tool. |

## Development dependencies

| Dependency | Why it is needed |
|---|---|
| `pytest`, `pytest-asyncio` | Test runner and async test support. |
| `pytest-httpx` | HTTP mocking for `MacroAgentClient` HTTP tests. |
| `ruff` | Linting and import sorting. |
| `mypy` | Static type checking. |
| `aiosqlite` | Async SQLite driver used by the test fixtures via SQLAlchemy. |

## Notes

- `structlog` is used directly for structured log emission. A machine-parseable
  JSON formatter (for example, `python-json-logger`) will be added once log
  aggregation is required in production; it is not needed in Phase 1.
- `httpx` is used instead of `aiohttp` because it is already a transitive
  dependency of FastAPI's test client and provides a consistent sync/async API.
