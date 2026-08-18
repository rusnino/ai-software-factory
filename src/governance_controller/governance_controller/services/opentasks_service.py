from governance_controller.schemas.task_contract import TaskContract


class OpenTasksService:
    """Stub service that materializes an approved Plane task subgraph into runtime
    opentasks.

    This Phase 1 implementation does not integrate with a real opentasks daemon or
    persist the runtime graph. It validates DAG structure and produces placeholder
    runtime IDs.
    """

    @classmethod
    def materialize(cls, task_contract: TaskContract) -> dict:
        return {
            "runtime_tasks": [
                {
                    "plane_task_id": task_contract.task_id,
                    "opentasks_id": f"OT-{task_contract.task_id}",
                    "dependencies": task_contract.dependencies,
                }
            ]
        }

    @classmethod
    def validate_dependencies(cls, task_contracts: list[TaskContract]) -> None:
        task_ids = {task.task_id for task in task_contracts}

        for task in task_contracts:
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(
                        f"Task {task.task_id} references unknown dependency: {dep}"
                    )

        graph: dict[str, list[str]] = {
            task.task_id: list(task.dependencies) for task in task_contracts
        }

        visiting: set[str] = set()
        visited: set[str] = set()

        def _visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError(f"Cycle detected involving task: {node}")

            visiting.add(node)
            for neighbor in graph.get(node, []):
                _visit(neighbor)
            visiting.remove(node)
            visited.add(node)

        for task_id in graph:
            _visit(task_id)
