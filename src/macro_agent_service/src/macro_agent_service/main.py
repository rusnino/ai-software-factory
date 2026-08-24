"""FastAPI entry point for the macro-agent service scaffold."""

import hmac
import os

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from macro_agent_service.config import config
from macro_agent_service.models import (
    FeedbackRequest,
    RunRequest,
    RunResponse,
    RunResult,
    RunStatus,
)
from macro_agent_service.store import store


class AuthError(Exception):
    """Raised when a request fails API secret authentication."""


def _require_secret(
    x_macro_agent_secret: str | None = Header(
        default=None, alias="X-Macro-Agent-Secret"
    ),
) -> None:
    """Validate the Controller shared secret when configured."""
    configured = config.api_secret
    if not configured:
        return
    if not hmac.compare_digest(x_macro_agent_secret or "", configured):
        raise AuthError("Invalid or missing macro-agent service secret")


async def _auth_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )

app = FastAPI(
    title="macro-agent service",
    description="Phase 2 scaffold for macro-agent run lifecycle.",
    version="0.1.0",
)
app.add_exception_handler(AuthError, _auth_exception_handler)


@app.post("/runs", status_code=status.HTTP_201_CREATED)
async def start_run(
    request: RunRequest,
    _authenticated: None = Depends(_require_secret),
) -> RunResponse:
    """Enqueue a new macro-agent run."""
    return await store.create(request)


@app.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    _authenticated: None = Depends(_require_secret),
) -> RunStatus:
    """Get the status of a run."""
    run = await store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
    return run


@app.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    _authenticated: None = Depends(_require_secret),
) -> RunStatus:
    """Cancel an active run."""
    run = await store.cancel(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
    return run


@app.get("/runs/{run_id}/collect")
async def collect_run(
    run_id: str,
    _authenticated: None = Depends(_require_secret),
) -> RunResult:
    """Collect the result of a run."""
    result = await store.collect(run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
    return result


@app.post("/runs/{run_id}/feedback")
async def feedback_run(
    run_id: str,
    request: FeedbackRequest,
    _authenticated: None = Depends(_require_secret),
) -> RunStatus:
    """Receive failure feedback from the Governance Controller."""
    result = await store.add_feedback(run_id, request)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
    return result


def main() -> None:
    """CLI entry point for the macro-agent service."""
    # Re-read environment so callers that set env vars after import see them.
    config.host = os.environ.get("MACRO_AGENT_SERVICE_HOST", config.host)
    config.port = int(os.environ.get("MACRO_AGENT_SERVICE_PORT", str(config.port)))
    uvicorn.run(app, host=config.host, port=config.port)
