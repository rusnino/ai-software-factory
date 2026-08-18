"""Tests for the Plane Adapter interface and in-memory stub."""

from governance_controller.adapters.plane_adapter import PlaneAdapter
from governance_controller.adapters.plane_adapter_memory import (
    MemoryPlaneAdapter,
)


def test_is_abstract_interface() -> None:
    assert issubclass(MemoryPlaneAdapter, PlaneAdapter)


def test_memory_adapter_records_sync_task_state() -> None:
    adapter = MemoryPlaneAdapter()
    adapter.sync_task_state("task-123", "READY")
    assert adapter.updates == [
        {"op": "sync_task_state", "task_id": "task-123", "state": "READY"},
    ]


def test_memory_adapter_records_add_comment() -> None:
    adapter = MemoryPlaneAdapter()
    adapter.add_comment("task-123", "human approved execution")
    assert adapter.updates == [
        {
            "op": "add_comment",
            "task_id": "task-123",
            "text": "human approved execution",
        },
    ]


def test_memory_adapter_records_sync_task_fields() -> None:
    adapter = MemoryPlaneAdapter()
    adapter.sync_task_fields("task-123", {"priority": "high", "labels": ["ai"]})
    assert adapter.updates == [
        {
            "op": "sync_task_fields",
            "task_id": "task-123",
            "fields": {"priority": "high", "labels": ["ai"]},
        },
    ]


def test_memory_adapter_records_multiple_calls_in_order() -> None:
    adapter = MemoryPlaneAdapter()
    adapter.sync_task_state("task-123", "READY")
    adapter.add_comment("task-123", "comment one")
    adapter.sync_task_fields("task-123", {"x": 1})

    assert [u["op"] for u in adapter.updates] == [
        "sync_task_state",
        "add_comment",
        "sync_task_fields",
    ]
