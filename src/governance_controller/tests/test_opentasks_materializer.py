"""Tests for the opentasks runtime DAG materializer."""

from typing import Any

import pytest

from governance_controller.services.opentasks_materializer import (
    MaterializerError,
    OpentasksMaterializer,
)


class _FakePlaneClient:
    def __init__(
        self,
        issues: dict[str, dict[str, Any]] | None = None,
        dependencies: dict[str, list[str]] | None = None,
    ) -> None:
        self.issues = issues or {}
        self.dependencies = dependencies or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def get_issue(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("get_issue", (issue_id,), {"project_id": project_id}))
        if issue_id not in self.issues:
            raise RuntimeError(f"Issue {issue_id} not found")
        return self.issues[issue_id]

    async def list_issue_dependencies(
        self,
        issue_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            ("list_issue_dependencies", (issue_id,), {"project_id": project_id})
        )
        deps = self.dependencies.get(issue_id, [])
        return {"results": [{"related_issue": {"id": dep}} for dep in deps]}


@pytest.fixture
def fake_client() -> _FakePlaneClient:
    return _FakePlaneClient(
        issues={
            "P-1": {
                "id": "P-1",
                "name": "Root task",
                "description_html": "Do the root thing",
                "state": {"name": "Approved"},
                "custom_properties": {"opentasks_id": "OT-1"},
            },
            "P-2": {
                "id": "P-2",
                "name": "Child task",
                "description_html": "Do the child thing",
                "state": {"name": "Approved"},
                "custom_properties": {"opentasks_id": "OT-2"},
            },
            "P-3": {
                "id": "P-3",
                "name": "Leaf task",
                "description_html": "Do the leaf thing",
                "state": {"name": "Approved"},
                "custom_properties": {"opentasks_id": "OT-3"},
            },
        },
        dependencies={
            "P-1": ["P-2"],
            "P-2": ["P-3"],
            "P-3": [],
        },
    )


async def test_materialize_linear_chain(fake_client: _FakePlaneClient) -> None:
    materializer = OpentasksMaterializer(client=fake_client)
    dag = await materializer.materialize("P-1", "proj-1")

    assert dag.project_id == "proj-1"
    assert len(dag.tasks) == 3
    ids = [t.id for t in dag.tasks]
    assert ids == ["OT-1", "OT-2", "OT-3"]

    task_by_id = {t.id: t for t in dag.tasks}
    assert task_by_id["OT-1"].dependencies == ["OT-2"]
    assert task_by_id["OT-2"].dependencies == ["OT-3"]
    assert task_by_id["OT-3"].dependencies == []


async def test_missing_dependency_raises(fake_client: _FakePlaneClient) -> None:
    # P-1 depends on P-2, but we also add P-missing which is never fetched
    # because it is not reachable as a dependency from P-1. Wait: the BFS
    # fetches dependencies, so P-missing would be fetched. Instead, make P-2
    # depend on P-missing and remove P-missing from issues so the BFS queues it
    # but cannot fetch it.
    fake_client.dependencies["P-2"] = ["P-missing"]
    fake_client.issues.pop("P-3", None)
    materializer = OpentasksMaterializer(client=fake_client)

    with pytest.raises(MaterializerError, match="Failed to fetch Plane issue"):
        await materializer.materialize("P-1", "proj-1")


async def test_cycle_raises(fake_client: _FakePlaneClient) -> None:
    fake_client.dependencies["P-3"] = ["P-1"]
    materializer = OpentasksMaterializer(client=fake_client)

    with pytest.raises(MaterializerError, match="cycle"):
        await materializer.materialize("P-1", "proj-1")


async def test_materializer_enforces_max_dag_size(
    fake_client: _FakePlaneClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DAG larger than opentasks_max_dag_size is rejected."""
    monkeypatch.setattr(
        "governance_controller.config.settings.opentasks_max_dag_size", 2
    )
    fake_client.issues = {
        "root": {"id": "root", "name": "Root"},
        "a": {"id": "a", "name": "A", "description_html": "desc A"},
        "b": {"id": "b", "name": "B", "description_html": "desc B"},
        "c": {"id": "c", "name": "C", "description_html": "desc C"},
    }
    fake_client.dependencies = {
        "root": ["a"],
        "a": ["b"],
        "b": ["c"],
    }

    materializer = OpentasksMaterializer(client=fake_client)
    with pytest.raises(MaterializerError, match="exceeded maximum size"):
        await materializer.materialize("root", project_id="proj-1")


async def test_deep_chain_does_not_crash_recursion(
    fake_client: _FakePlaneClient,
) -> None:
    # Build a deep linear chain to validate recursion guard / iterative safety.
    issues: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, list[str]] = {}
    count = 50
    for i in range(count):
        issues[f"P-{i}"] = {
            "id": f"P-{i}",
            "name": f"Task {i}",
            "state": {"name": "Approved"},
            "custom_properties": {"opentasks_id": f"OT-{i}"},
        }
        dependencies[f"P-{i}"] = [f"P-{i + 1}"] if i < count - 1 else []
    fake_client.issues = issues
    fake_client.dependencies = dependencies

    materializer = OpentasksMaterializer(client=fake_client)
    dag = await materializer.materialize("P-0", "proj-1")

    assert len(dag.tasks) == count
    assert all(t.id.startswith("OT-") for t in dag.tasks)


async def test_materialize_without_plane_config_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from governance_controller import config

    monkeypatch.setattr(config.settings, "plane_base_url", "")
    materializer = OpentasksMaterializer()

    with pytest.raises(MaterializerError, match="Plane integration is not configured"):
        await materializer.materialize("P-1", "proj-1")


async def test_opentasks_id_fallback(fake_client: _FakePlaneClient) -> None:
    fake_client.issues["P-1"]["custom_properties"] = {}
    materializer = OpentasksMaterializer(client=fake_client)
    dag = await materializer.materialize("P-1", "proj-1")

    task_by_plane = {t.plane_task_id: t for t in dag.tasks}
    assert task_by_plane["P-1"].id == "OT-P-1"
