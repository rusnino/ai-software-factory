"""Tests for the OpenTasksService stub materializer."""

import pytest

from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.opentasks_service import OpenTasksService


def _make_contract(
    task_id: str,
    dependencies: list[str] | None = None,
) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        project_id="proj-1",
        objective="Test task",
        acceptance=["passes tests"],
        dependencies=dependencies or [],
    )


class TestOpenTasksServiceMaterialize:
    def test_materialize_returns_expected_runtime_task(self) -> None:
        contract = _make_contract(
            task_id="task-1",
            dependencies=["task-0"],
        )

        result = OpenTasksService.materialize(contract)

        assert result == {
            "runtime_tasks": [
                {
                    "plane_task_id": "task-1",
                    "opentasks_id": "OT-task-1",
                    "dependencies": ["task-0"],
                }
            ]
        }


class TestOpenTasksServiceValidateDependencies:
    def test_valid_acyclic_graph_passes(self) -> None:
        contracts = [
            _make_contract("task-1", dependencies=["task-2"]),
            _make_contract("task-2", dependencies=["task-3"]),
            _make_contract("task-3"),
        ]

        OpenTasksService.validate_dependencies(contracts)

    def test_missing_dependency_raises(self) -> None:
        contracts = [
            _make_contract("task-1", dependencies=["missing-task"]),
        ]

        with pytest.raises(ValueError, match="unknown dependency: missing-task"):
            OpenTasksService.validate_dependencies(contracts)

    def test_self_reference_cycle_raises(self) -> None:
        contracts = [
            _make_contract("task-1", dependencies=["task-1"]),
        ]

        with pytest.raises(ValueError, match="Cycle detected"):
            OpenTasksService.validate_dependencies(contracts)

    def test_multi_node_cycle_raises(self) -> None:
        contracts = [
            _make_contract("task-1", dependencies=["task-2"]),
            _make_contract("task-2", dependencies=["task-3"]),
            _make_contract("task-3", dependencies=["task-1"]),
        ]

        with pytest.raises(ValueError, match="Cycle detected"):
            OpenTasksService.validate_dependencies(contracts)
