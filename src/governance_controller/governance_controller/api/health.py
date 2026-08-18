"""Health check REST API endpoint."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.db import get_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str = "0.1.0"


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Return API and database connectivity status."""
    try:
        await db.execute(text("select 1"))
        return HealthResponse(
            status="ok",
            database="connected",
            version="0.1.0",
        )
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "degraded",
                "database": "disconnected",
                "version": "0.1.0",
            },
        )
