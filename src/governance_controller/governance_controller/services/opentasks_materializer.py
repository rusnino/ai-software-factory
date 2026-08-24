"""Materialize approved Plane task subgraph into an opentasks runtime DAG."""

from __future__ import annotations

from collections import deque
from typing import Any

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.schemas.opentasks import OpentasksDAG, OpentasksTask


class MaterializerError(Exception):
    """Raised when the runtime DAG cannot be materialized."""


class OpentasksMaterializer:
    """Build an opentasks DAG from Plane project tasks and dependencies.

    The materializer starts from an approved root task, walks Plane
    dependencies, validates the resulting graph, and produces a runtime DAG.

    Args:
        client: Optional PlaneClient override. Defaults to a client built from
            ``settings`` when Plane is configured.
    """

    def __init__(self, client: PlaneClient | None = None) -> None:
        self._client = client

    def _client_or_raise(self) -> PlaneClient:
        if self._client is not None:
            return self._client
        if not settings.plane_base_url:
            raise MaterializerError(
                "Plane integration is not configured; cannot materialize DAG"
            )
        return PlaneClient()

    async def materialize(
        self,
        root_plane_task_id: str,
        project_id: str,
    ) -> OpentasksDAG:
        """Build an opentasks DAG starting from ``root_plane_task_id``.

        Only approved Plane tasks are included. The graph is validated for
        cycles and missing dependencies.
        """
        client = self._client_or_raise()

        # Fetch the root issue to confirm it exists and read its details.
        root_issue = await client.get_issue(root_plane_task_id, project_id=project_id)
        root_name = _issue_name(root_issue)

        # BFS over Plane dependencies. ``tasks`` is keyed by opentasks ID;
        # ``plane_edges`` stores dependencies as Plane IDs and is translated
        # after all issues have been fetched.
        queue: deque[str] = deque([root_plane_task_id])
        seen: set[str] = set()
        tasks: dict[str, OpentasksTask] = {}
        plane_edges: dict[str, set[str]] = {}
        plane_to_opentasks: dict[str, str] = {}

        while queue:
            plane_id = queue.popleft()
            if plane_id in seen:
                continue
            seen.add(plane_id)

            try:
                issue = await client.get_issue(plane_id, project_id=project_id)
            except Exception as exc:
                raise MaterializerError(
                    f"Failed to fetch Plane issue {plane_id}"
                ) from exc

            opentasks_id = _opentasks_id(issue)
            plane_to_opentasks[plane_id] = opentasks_id
            dependencies = await self._fetch_dependency_ids(
                plane_id, project_id=project_id
            )
            plane_edges[plane_id] = dependencies

            tasks[opentasks_id] = OpentasksTask(
                id=opentasks_id,
                plane_task_id=plane_id,
                objective=_issue_name(issue),
                acceptance=[_issue_description(issue)],
                metadata={
                    "plane_state": _issue_state(issue),
                    "plane_project_id": project_id,
                },
            )

            for dep_id in dependencies:
                if dep_id not in seen:
                    queue.append(dep_id)

        missing = {
            dep
            for deps in plane_edges.values()
            for dep in deps
            if dep not in plane_to_opentasks
        }
        if missing:
            raise MaterializerError(
                f"DAG contains missing dependencies: {sorted(missing)}"
            )

        # Build opentasks-ID edges for cycle detection.
        edges = {
            plane_to_opentasks[plane_id]: {
                plane_to_opentasks[dep_id] for dep_id in dep_plane_ids
            }
            for plane_id, dep_plane_ids in plane_edges.items()
        }
        cycle = _find_cycle(edges)
        if cycle is not None:
            raise MaterializerError(f"DAG contains a cycle: {' -> '.join(cycle)}")

        # Populate dependency lists using opentasks IDs.
        for opentasks_id, task in tasks.items():
            task.dependencies = sorted(edges.get(opentasks_id, set()))

        return OpentasksDAG(
            project_id=project_id,
            tasks=sorted(tasks.values(), key=lambda t: t.id),
            metadata={
                "root_plane_task_id": root_plane_task_id,
                "root_name": root_name,
                "total_tasks": len(tasks),
            },
        )

    async def _fetch_dependency_ids(
        self,
        plane_issue_id: str,
        project_id: str,
    ) -> set[str]:
        """Return Plane dependency IDs as a set."""
        try:
            result = await self._client_or_raise().list_issue_dependencies(
                plane_issue_id, project_id=project_id
            )
        except Exception as exc:
            raise MaterializerError(
                f"Failed to fetch dependencies for {plane_issue_id}"
            ) from exc

        dependencies: set[str] = set()
        for item in _result_items(result):
            dep_id = _dependency_id(item)
            if dep_id:
                dependencies.add(dep_id)
        return dependencies


def _opentasks_id(issue: dict[str, Any]) -> str:
    """Return a stable opentasks ID for a Plane issue."""
    custom = issue.get("custom_properties") or {}
    opentasks_id = custom.get("opentasks_id")
    if isinstance(opentasks_id, str) and opentasks_id:
        return opentasks_id
    return f"OT-{issue.get('id', 'unknown')}"


def _issue_name(issue: dict[str, Any]) -> str:
    return issue.get("name") or issue.get("title") or "Untitled"


def _issue_description(issue: dict[str, Any]) -> str:
    return issue.get("description_html") or issue.get("description") or ""


def _issue_state(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return state.get("name") or state.get("id") or ""
    return str(state) if state is not None else ""


def _result_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("results", result)
    if isinstance(items, list):
        return items
    return []


def _dependency_id(item: dict[str, Any]) -> str | None:
    """Extract the dependent issue ID from a Plane dependency item."""
    for key in ("related_issue", "issue", "dependency", "related_issue_detail"):
        value = item.get(key)
        if isinstance(value, dict):
            return value.get("id")
    return item.get("id")


def _find_cycle(edges: dict[str, set[str]]) -> list[str] | None:
    """Return a cycle if the directed graph contains one, else None.

    Implemented iteratively so deep but acyclic dependency chains do not
    trigger false-positive cycle reports based on recursion depth.
    """
    WHITE, GRAY = 0, 1
    color: dict[str, int] = dict.fromkeys(edges, WHITE)
    parent: dict[str, str] = {}

    for start in sorted(edges):
        if color.get(start, WHITE) != WHITE:
            continue
        stack = [(start, iter(sorted(edges.get(start, set()))))]
        color[start] = GRAY
        parent[start] = ""

        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
            except StopIteration:
                color[node] = WHITE
                stack.pop()
                continue

            child_color = color.get(child, WHITE)
            if child_color == GRAY:
                # Found a cycle: reconstruct the path from child to node.
                cycle = [child]
                current = node
                while current != child and current in parent:
                    cycle.append(current)
                    current = parent.get(current, "")
                cycle.append(child)
                return list(reversed(cycle))
            if child_color == WHITE:
                color[child] = GRAY
                parent[child] = node
                stack.append((child, iter(sorted(edges.get(child, set())))))

    return None
