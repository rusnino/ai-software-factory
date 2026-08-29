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
    from macro_agent_service import main as main_module
    from macro_agent_service import store as store_module
    from macro_agent_service.config import config

    isolated = RunStore()
    monkeypatch.setattr(store_module, "store", isolated)
    monkeypatch.setattr(main_module, "store", isolated)
    monkeypatch.setattr(config, "api_secret", "")
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


async def test_missing_secret_returns_401(
    fresh_store: RunStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from macro_agent_service.config import config

    monkeypatch.setattr(config, "api_secret", "secret")
    async with _client() as client:
        response = await client.post(
            "/runs",
            json={"task_id": "task-1", "objective": "x"},
        )
    assert response.status_code == 401


async def test_start_run_accepts_opentasks_dag(fresh_store: RunStore) -> None:
    """#220: opentasks DAG must be accepted and stored, not silently dropped."""
    dag = {
        "project_id": "proj-1",
        "tasks": [
            {
                "id": "OT-1",
                "plane_task_id": "P-1",
                "objective": "Root",
                "acceptance": ["done"],
                "dependencies": ["OT-2"],
            }
        ],
    }
    async with _client() as client:
        response = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
                "acceptance": ["Tests pass"],
                "opentasks_dag": dag,
            },
        )

    assert response.status_code == 201
    body = response.json()
    run_id = body["run_id"]

    snapshot = fresh_store.snapshot()
    stored_request = snapshot[run_id]["request"]
    assert stored_request["opentasks_dag"] == dag


async def test_valid_secret_allowed(
    fresh_store: RunStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from macro_agent_service.config import config

    monkeypatch.setattr(config, "api_secret", "secret")
    async with _client() as client:
        response = await client.post(
            "/runs",
            json={"task_id": "task-1", "objective": "x"},
            headers={"X-Macro-Agent-Secret": "secret"},
        )
    assert response.status_code == 201


async def test_feedback_on_cancelled_run_returns_409(fresh_store: RunStore) -> None:
    """#246: feedback must be rejected for terminal-state runs."""
    async with _client() as client:
        created = await client.post(
            "/runs",
            json={"task_id": "task-1", "objective": "Implement a feature"},
        )
        run_id = created.json()["run_id"]
        await client.post(f"/runs/{run_id}/cancel")

        response = await client.post(
            f"/runs/{run_id}/feedback",
            json={
                "controller_task_id": "task-1",
                "controller_state": "RUNNING",
                "verification_report": {},
                "execution_attempts": 1,
                "max_retries": 2,
                "objective": "Implement a feature",
            },
        )

    assert response.status_code == 409


async def test_run_store_evicts_oldest_terminal_run(fresh_store: RunStore) -> None:
    """#243: terminal runs are evicted when the in-memory store reaches its cap."""
    capped = RunStore(max_runs=3)
    from macro_agent_service import main as main_module
    from macro_agent_service import store as store_module

    # Patch the module-level singleton used by the app for this test only.
    store_module.store = capped
    main_module.store = capped

    created_ids: list[str] = []
    for idx in range(3):
        response = await capped.create(
            type(
                "RunRequest",
                (),
                {
                    "model_dump": (
                        lambda idx=idx: {  # noqa: ARG005
                            "task_id": f"task-{idx}",
                            "objective": f"obj-{idx}",
                        }
                    )
                },
            )()
        )
        created_ids.append(response.run_id)

    # Mark the first run terminal, then create a fourth run. The first should
    # be evicted, while the remaining two active runs stay.
    capped._runs[created_ids[0]]["status"] = "cancelled"
    response = await capped.create(
        type(
            "RunRequest",
            (),
            {
                "model_dump": lambda self: {  # noqa: ARG005
                    "task_id": "task-3",
                    "objective": "obj-3",
                }
            },
        )()
    )

    assert created_ids[0] not in capped._runs
    assert created_ids[1] in capped._runs
    assert created_ids[2] in capped._runs
    assert response.run_id in capped._runs
