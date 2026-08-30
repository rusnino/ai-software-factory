"""#276: connection-pool exhaustion must return a clean 503, not a raw 500."""

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from governance_controller.db import get_db
from governance_controller.main import app
from governance_controller.models.task import Task


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


pytestmark = pytest.mark.skipif(
    not _is_postgres(os.environ.get("GC_TEST_DATABASE_URL", "")),
    reason="requires a real PostgreSQL database via GC_TEST_DATABASE_URL "
    "(the ACCESS EXCLUSIVE lock technique needs real row/table locking)",
)

_CONTROLLER_SECRET = "pool-exhaustion-secret"


async def test_pool_checkout_timeout_returns_503_not_generic_500() -> None:
    """A real, deliberately tiny connection pool exhausted by real Postgres
    lock contention must surface as a clean 503 with the documented body and
    Retry-After header, not fall through to a raw, unstructured 500.

    Mirrors the live reproduction that found #276: a small pool
    (``pool_size=1, max_overflow=0``, ``pool_timeout`` short) plus a real
    ``ACCESS EXCLUSIVE`` lock held on the ``task`` table by a separate
    connection, then more concurrent requests than the pool can serve.
    """
    from governance_controller.config import settings

    url = os.environ.get("GC_TEST_DATABASE_URL", "")

    tiny_engine = create_async_engine(
        url,
        echo=False,
        future=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    tiny_session_local = sessionmaker(
        bind=tiny_engine, class_=AsyncSession, expire_on_commit=False
    )

    lock_engine = create_async_engine(url, echo=False, future=True)

    original_secret = settings.controller_api_secret
    settings.controller_api_secret = _CONTROLLER_SECRET

    async def _override_get_db():
        async with tiny_session_local() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        seeded_id = "task-pool-exhaustion-276"
        async with tiny_session_local() as seed:
            await seed.execute(
                text("DELETE FROM task WHERE id = :id"), {"id": seeded_id}
            )
            seed.add(
                Task(id=seeded_id, project_id="proj-1", proposed_by="agent-1")
            )
            await seed.commit()

        async def _hold_lock() -> None:
            async with lock_engine.begin() as conn:
                await conn.execute(text("LOCK TABLE task IN ACCESS EXCLUSIVE MODE"))
                await asyncio.sleep(3)

        lock_task = asyncio.create_task(_hold_lock())
        await asyncio.sleep(0.3)  # let the lock actually take hold first

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                requests = [
                    client.get(
                        f"/tasks/{seeded_id}",
                        headers={"X-Controller-Secret": _CONTROLLER_SECRET},
                    )
                    for _ in range(4)
                ]
                responses = await asyncio.gather(*requests, return_exceptions=True)
        finally:
            await lock_task

        statuses = [
            r.status_code for r in responses if not isinstance(r, BaseException)
        ]
        assert 503 in statuses, (
            f"expected at least one 503 among genuinely pool-starved "
            f"requests, got: {statuses}"
        )
        assert 500 not in statuses, "must never fall through to a raw 500"

        timeout_response = next(
            r
            for r in responses
            if not isinstance(r, BaseException) and r.status_code == 503
        )
        assert timeout_response.json() == {
            "detail": "Database pool exhausted, retry later"
        }
        assert "Retry-After" in timeout_response.headers
    finally:
        settings.controller_api_secret = original_secret
        app.dependency_overrides.pop(get_db, None)
        cleanup_engine = create_async_engine(url, echo=False, future=True)
        try:
            async with cleanup_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM task WHERE id = :id"),
                    {"id": "task-pool-exhaustion-276"},
                )
        finally:
            await cleanup_engine.dispose()
        await tiny_engine.dispose()
        await lock_engine.dispose()
