"""FastAPI application entry point for the Governance Controller."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from governance_controller.api import approvals as approvals_api
from governance_controller.api import audit as audit_api
from governance_controller.api import events as events_api
from governance_controller.api import executions as executions_api
from governance_controller.api import health as health_api
from governance_controller.api import tasks as tasks_api
from governance_controller.config import settings
from governance_controller.db import init_db
from governance_controller.middleware import WriteBodySizeLimitMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Initialize the database on startup when configured for PostgreSQL.

    The full migration path (ALTER TABLE backfill, DB-level immutability
    triggers) is intentionally Postgres-only; AGENTS.md mandates PostgreSQL for
    production Controller deployments. SQLite-backed dev/test deployments only
    run ``create_all()`` via ``ensure_sqlite_tables()``.
    """
    if settings.database_url.startswith("postgresql"):
        await init_db()
    yield


app = FastAPI(
    title="Governance Controller",
    description=(
        "Authoritative approval, state machine, and policy API "
        "for the AI Software Factory."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(WriteBodySizeLimitMiddleware)

app.include_router(health_api.router)
app.include_router(tasks_api.router)
app.include_router(approvals_api.router)
app.include_router(events_api.router)
app.include_router(executions_api.router)
app.include_router(audit_api.router)
