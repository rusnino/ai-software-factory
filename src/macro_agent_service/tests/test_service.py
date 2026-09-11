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


async def test_start_run_is_idempotent_by_controller_execution_id(
    fresh_store: RunStore,
) -> None:
    """#301: POST /runs is idempotent by controller_execution_id metadata."""
    async with _client() as client:
        first = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Implement a feature",
                "acceptance": ["Tests pass"],
                "metadata": {"controller_execution_id": "exec-orphan-301"},
            },
        )
        assert first.status_code == 201
        first_id = first.json()["run_id"]

        second = await client.post(
            "/runs",
            json={
                "task_id": "task-1",
                "objective": "Different objective after response loss",
                "acceptance": ["Tests pass"],
                "metadata": {"controller_execution_id": "exec-orphan-301"},
            },
        )
        assert second.status_code == 201
        assert second.json()["run_id"] == first_id
        assert len(fresh_store._runs) == 1


async def test_collect_on_non_terminal_run_preserves_idempotency(
    fresh_store: RunStore,
) -> None:
    """#353: collecting an active run must not release its idempotency key."""
    async with _client() as client:
        first = await client.post(
            "/runs",
            json={
                "task_id": "task-collect",
                "objective": "Active run for idempotency",
                "metadata": {"controller_execution_id": "exec-collect-353"},
            },
        )
        first_id = first.json()["run_id"]

        # Collect while still queued/non-terminal.
        collect_resp = await client.get(f"/runs/{first_id}/collect")
        assert collect_resp.status_code == 200

        # Retry with the same controller_execution_id must return the original
        # run, not mint a duplicate.
        retry = await client.post(
            "/runs",
            json={
                "task_id": "task-collect",
                "objective": "Retry after premature collect",
                "metadata": {"controller_execution_id": "exec-collect-353"},
            },
        )
        assert retry.status_code == 201
        assert retry.json()["run_id"] == first_id
        assert len(fresh_store._runs) == 1


async def test_cancel_releases_idempotency_protection(
    fresh_store: RunStore,
) -> None:
    """#354: cancelling a protected run must make it evictable without GET/collect."""
    capped = RunStore(max_runs=3)
    from macro_agent_service import main as main_module
    from macro_agent_service import store as store_module

    store_module.store = capped
    main_module.store = capped

    async with _client() as client:
        created = await client.post(
            "/runs",
            json={
                "task_id": "task-cancel",
                "objective": "Run to be cancelled",
                "metadata": {"controller_execution_id": "exec-cancel-354"},
            },
        )
        run_id = created.json()["run_id"]

        # Cancel the run and do NOT call GET or collect afterwards.
        cancel_resp = await client.post(f"/runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200

        # The cancelled run must no longer block eviction.
        filler1 = await client.post(
            "/runs",
            json={
                "task_id": "task-filler-1",
                "objective": "Filler 1",
            },
        )
        await client.post(f"/runs/{filler1.json()['run_id']}/cancel")
        filler2 = await client.post(
            "/runs",
            json={
                "task_id": "task-filler-2",
                "objective": "Filler 2",
            },
        )
        await client.post(f"/runs/{filler2.json()['run_id']}/cancel")
        filler3 = await client.post(
            "/runs",
            json={
                "task_id": "task-filler-3",
                "objective": "Filler 3",
            },
        )
        await client.post(f"/runs/{filler3.json()['run_id']}/cancel")

        assert len(capped._runs) == 3
        assert run_id not in capped._runs


async def test_idempotency_releases_on_terminal_status_observation(
    fresh_store: RunStore,
) -> None:
    """#350: terminal-status observation (not collect) must release protection."""
    capped = RunStore(max_runs=5)
    from macro_agent_service import main as main_module
    from macro_agent_service import store as store_module

    store_module.store = capped
    main_module.store = capped

    async with _client() as client:
        run_ids: list[str] = []
        for idx in range(5):
            created = await client.post(
                "/runs",
                json={
                    "task_id": f"task-cap-{idx}",
                    "objective": "Fill capacity",
                    "metadata": {
                        "controller_execution_id": f"exec-cap-{idx}",
                    },
                },
            )
            run_id = created.json()["run_id"]
            run_ids.append(run_id)
            await client.post(f"/runs/{run_id}/cancel")
            # Controller observes terminal status through GET, not collect().
            await client.get(f"/runs/{run_id}")

        # All 5 idempotency keys should now be released, leaving room for a new
        # run despite the cap.
        extra = await client.post(
            "/runs",
            json={
                "task_id": "task-extra",
                "objective": "New run after steady-state churn",
                "metadata": {"controller_execution_id": "exec-extra-350"},
            },
        )
        assert extra.status_code == 201
        assert len(capped._runs) == 5
        # Oldest run was evicted; its idempotency key is gone.
        assert run_ids[0] not in capped._runs


async def test_idempotency_survives_capacity_eviction(fresh_store: RunStore) -> None:
    """#348: an active idempotency-protected run must not be evicted."""
    capped = RunStore(max_runs=3)
    from macro_agent_service import main as main_module
    from macro_agent_service import store as store_module

    store_module.store = capped
    main_module.store = capped

    async with _client() as client:
        first = await client.post(
            "/runs",
            json={
                "task_id": "task-evict",
                "objective": "First run for execution A",
                "metadata": {"controller_execution_id": "exec-evict-A"},
            },
        )
        assert first.status_code == 201
        first_id = first.json()["run_id"]

        # The protected run stays active (queued). Push the store past its cap
        # with unrelated terminal runs; the active protected run must survive.
        for idx in range(3):
            created = await client.post(
                "/runs",
                json={
                    "task_id": f"task-filler-{idx}",
                    "objective": "Filler run",
                },
            )
            await client.post(f"/runs/{created.json()['run_id']}/cancel")

        # Retry the original execution id; the protected run must still exist.
        retry = await client.post(
            "/runs",
            json={
                "task_id": "task-evict",
                "objective": "Retry after churn",
                "metadata": {"controller_execution_id": "exec-evict-A"},
            },
        )
        assert retry.status_code == 201
        assert retry.json()["run_id"] == first_id
        assert first_id in capped._runs
        assert len(capped._runs) == 3
        assert first_id in capped._runs


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
