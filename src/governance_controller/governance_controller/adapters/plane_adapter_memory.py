"""In-memory stub implementation of the Plane Adapter interface.

This implementation records every synchronization call for inspection in
Phase 1 tests. It performs no I/O and has no dependency on Plane CE.
"""

from governance_controller.adapters.plane_adapter import PlaneAdapter


class MemoryPlaneAdapter(PlaneAdapter):
    """Records Plane Adapter calls in memory for testing and local dev.

    Attributes:
        updates: A chronological list of recorded calls. Each entry is a dict
            with keys ``op``, ``task_id`` and any operation-specific fields.
    """

    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def sync_task_state(self, task_id: str, state: str) -> None:
        self.updates.append({
            "op": "sync_task_state",
            "task_id": task_id,
            "state": state,
        })

    def sync_task_fields(self, task_id: str, fields: dict[str, object]) -> None:
        self.updates.append({
            "op": "sync_task_fields",
            "task_id": task_id,
            "fields": fields,
        })

    def add_comment(self, task_id: str, text: str) -> None:
        self.updates.append({
            "op": "add_comment",
            "task_id": task_id,
            "text": text,
        })
