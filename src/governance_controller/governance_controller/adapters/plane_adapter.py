"""Abstract Plane Adapter interface.

The Governance Controller uses this interface to project internal task state
and audit events to Plane CE. Implementations must not be relied upon for
workflow authority; Plane CE is strictly a human-facing projection.
"""

from abc import ABC, abstractmethod


class PlaneAdapter(ABC):
    """Abstract interface for synchronizing task state with Plane CE."""

    @abstractmethod
    def sync_task_state(self, task_id: str, state: str) -> None:
        """Synchronize the high-level state of a task with Plane CE."""

    @abstractmethod
    def sync_task_fields(self, task_id: str, fields: dict) -> None:
        """Synchronize arbitrary task fields with Plane CE."""

    @abstractmethod
    def add_comment(self, task_id: str, text: str) -> None:
        """Add a comment to a task in Plane CE."""
