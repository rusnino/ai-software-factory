"""Tests for the macro-agent service scaffold."""


import pytest
from httpx import ASGITransport, AsyncClient

from macro_agent_service.main import app
from macro_agent_service.store import RunStore


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> RunStore:
    """Provide an isolated run store for each test."""
    from macro_agent_service import store as store_module

    isolated = RunStore()
    monkeypatch.setattr(store_module, "store", isolated)
    return isolated


async def test_start_run_returns_queued(fresh_store: RunStore) -> None:
    async with _client() as client:
        response = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
                "acceptance": ["Tests pass"],
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert "run_id" in body


async def test_get_run_returns_status(fresh_store: RunStore) -> None:
    async with _client() as client:
        created = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
            },
        )
        run_id = created.json()["run_id"]

        response = await client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


async def test_get_missing_run_returns_404(fresh_store: RunStore) -> None:
    async with _client() as client:
        response = await client.get("/runs/missing")

    assert response.status_code == 404


async def test_cancel_run(fresh_store: RunStore) -> None:
    async with _client() as client:
        created = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
            },
        )
        run_id = created.json()["run_id"]

        response = await client.post(f"/runs/{run_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_collect_run(fresh_store: RunStore) -> None:
    async with _client() as client:
        created = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
            },
        )
        run_id = created.json()["run_id"]

        response = await client.get(f"/runs/{run_id}/collect")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
