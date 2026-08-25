"""FastAPI application entry point for the Governance Controller."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from governance_controller.api import approvals as approvals_api
from governance_controller.api import audit as audit_api
from governance_controller.api import events as events_api
from governance_controller.api import executions as executions_api
from governance_controller.api import health as health_api
from governance_controller.api import intake as intake_api
from governance_controller.api import tasks as tasks_api
from governance_controller.api import webhooks as webhooks_api
from governance_controller.api.auth import ControllerAuthError
from governance_controller.api.events import EventAuthError
from governance_controller.api.intake import IntakeAuthError
from governance_controller.api.webhooks import WebhookAuthError
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


@app.exception_handler(WebhookAuthError)
async def _webhook_auth_exception_handler(
    _request: Request,
    exc: WebhookAuthError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )


@app.exception_handler(IntakeAuthError)
async def _intake_auth_exception_handler(
    _request: Request,
    exc: IntakeAuthError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )


@app.exception_handler(EventAuthError)
async def _event_auth_exception_handler(
    _request: Request,
    exc: EventAuthError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )


@app.exception_handler(ControllerAuthError)
async def _controller_auth_exception_handler(
    _request: Request,
    exc: ControllerAuthError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )


app.include_router(health_api.router)
app.include_router(tasks_api.router)
app.include_router(approvals_api.router)
app.include_router(events_api.router)
app.include_router(executions_api.router)
app.include_router(audit_api.router)
app.include_router(webhooks_api.router)
app.include_router(intake_api.router)
