"""Reconciliation service for Plane/Controller divergence detection.

Phase 1 implementation: in-process, best-effort consistency check between the
Controller's task state and a Plane adapter projection. A future phase will
wire this into a durable scheduler (e.g., Celery/Temporal) and use a real
Plane CE adapter.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_adapter import PlaneAdapter
from governance_controller.adapters.plane_adapter_memory import MemoryPlaneAdapter
from governance_controller.models.task import Task
from governance_controller.services.audit_service import AuditService


class ReconciliationService:
    """Detect and log divergence between Controller state and Plane state."""

    def __init__(
        self,
        db: AsyncSession,
        plane_adapter: PlaneAdapter | None = None,
    ) -> None:
        """Initialize the reconciliation service.

        Args:
            db: Async SQLAlchemy session.
            plane_adapter: Adapter used to read/write Plane state. Defaults to
                an in-memory stub for Phase 1.
        """
        self.db = db
        self.plane_adapter = plane_adapter or MemoryPlaneAdapter()

    async def check(self, task_id: str) -> dict:
        """Check whether the Controller task state matches Plane's projection.

        Args:
            task_id: The task to reconcile.

        Returns:
            A report dict with ``task_id``, ``controller_state``, ``plane_state``,
            ``diverged`` and ``resolved`` keys.
        """
        task = await self.db.scalar(select(Task).where(Task.id == task_id))
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        controller_state = task.state.value

        # For the in-memory stub, derive the last known Plane state from
        # recorded sync_task_state updates. A real adapter would query Plane.
        plane_state = controller_state
        if isinstance(self.plane_adapter, MemoryPlaneAdapter):
            state_updates = [
                u["state"]
                for u in self.plane_adapter.updates
                if u["op"] == "sync_task_state" and u["task_id"] == task_id
            ]
            if state_updates:
                plane_state = state_updates[-1]

        diverged = plane_state != controller_state
        resolved = not diverged

        await AuditService.log(
            db=self.db,
            event_type="reconciliation_check",
            task_id=task_id,
            actor="system",
            source="reconciliation",
            payload={
                "controller_state": controller_state,
                "plane_state": plane_state,
                "diverged": diverged,
            },
        )

        return {
            "task_id": task_id,
            "controller_state": controller_state,
            "plane_state": plane_state,
            "diverged": diverged,
            "resolved": resolved,
        }
