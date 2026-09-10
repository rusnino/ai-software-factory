"""In-memory run store for the scaffold."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

from fastapi import HTTPException, status

from macro_agent_service.models import (
    FeedbackRequest,
    RunRequest,
    RunResponse,
    RunResult,
    RunStatus,
)

_MAX_RUNS = 10_000
_TERMINAL_STATUSES = {"done", "failed", "cancelled"}


class RunStore:
    """Simple in-memory store for macro-agent runs.

    This is intentionally minimal for Phase 2 scaffolding. A future
    implementation will replace it with persistent storage and a real
    macro-agent backend.
    """

    def __init__(self, max_runs: int = _MAX_RUNS) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._max_runs = max_runs
        # Maps controller_execution_id -> run_id for idempotency protection.
        self._idempotency_keys: dict[str, str] = {}

    @staticmethod
    def _idempotency_key(run: dict[str, Any]) -> str | None:
        return cast(
            str | None,
            run.get("request", {}).get("metadata", {}).get(
                "controller_execution_id"
            ),
        )

    def _is_idempotency_protected(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        key = self._idempotency_key(run)
        return key is not None and self._idempotency_keys.get(key) == run_id

    def _release_idempotency_key(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        key = self._idempotency_key(run)
        if key and self._idempotency_keys.get(key) == run_id:
            del self._idempotency_keys[key]

    def _evict_if_needed(self) -> None:
        """Drop oldest terminal runs when the store reaches its cap.

        Runs that are still valid idempotency targets (have a live
        controller_execution_id mapping) are never evicted. This preserves the
        idempotency contract from #301 under capacity pressure.

        Prefer collected terminal runs, then any terminal runs. Raises
        HTTPException when no evictable terminal runs remain.
        """
        while len(self._runs) >= self._max_runs:
            collected_terminal_keys = [
                run_id
                for run_id, run in self._runs.items()
                if run["status"] in _TERMINAL_STATUSES
                and run.get("collected", False)
                and not self._is_idempotency_protected(run_id)
            ]
            terminal_keys = collected_terminal_keys or [
                run_id
                for run_id, run in self._runs.items()
                if run["status"] in _TERMINAL_STATUSES
                and not self._is_idempotency_protected(run_id)
            ]
            if not terminal_keys:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Run store capacity exhausted; no terminal runs to evict",
                )
            oldest = min(terminal_keys, key=lambda k: self._runs[k]["created_at"])
            self._release_idempotency_key(oldest)
            del self._runs[oldest]

    def _mark_collected(self, run_id: str) -> None:
        """Mark a run as observed so it can be evicted before uncollected ones."""
        run = self._runs.get(run_id)
        if run is not None:
            run["collected"] = True

    def _run_by_controller_execution_id(
        self, controller_execution_id: str
    ) -> dict[str, Any] | None:
        """Return an existing run that was started for the given execution id."""
        for run in self._runs.values():
            meta = run.get("request", {}).get("metadata", {})
            if meta.get("controller_execution_id") == controller_execution_id:
                return run
        return None

    async def create(self, request: RunRequest) -> RunResponse:
        """Create a new run and return its handle.

        Idempotent by controller_execution_id: a request whose metadata
        carries the same execution id as an existing run returns the existing
        run id. This lets the Governance Controller recover the external run
        after a response loss without creating a duplicate.
        """
        import time

        metadata = getattr(request, "metadata", None) or {}
        controller_execution_id = metadata.get("controller_execution_id")
        if controller_execution_id:
            existing_run_id = self._idempotency_keys.get(
                controller_execution_id
            )
            if existing_run_id is not None:
                existing = self._runs.get(existing_run_id)
                if existing is not None:
                    return RunResponse(
                        run_id=existing["run_id"], status=existing["status"]
                    )
                # Stale mapping; the run was evicted by an explicit release.
                del self._idempotency_keys[controller_execution_id]

        self._evict_if_needed()
        run_id = str(uuid.uuid4())
        self._runs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "request": request.model_dump(),
            "result": None,
            "created_at": time.monotonic(),
        }
        if controller_execution_id:
            self._idempotency_keys[controller_execution_id] = run_id
        return RunResponse(run_id=run_id, status="queued")

    async def get(self, run_id: str) -> RunStatus | None:
        """Return the run status, or None if not found."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run["status"] in _TERMINAL_STATUSES:
            self._mark_collected(run_id)
            # The Controller observes terminal status through status polling
            # rather than collect(), so this is the real steady-state release
            # path for idempotency protection (#350).
            self._release_idempotency_key(run_id)
        return RunStatus(
            run_id=run_id,
            status=run["status"],
            metadata={"request": run["request"]},
        )

    async def cancel(self, run_id: str) -> RunStatus | None:
        """Cancel a run if it exists and is still active."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run["status"] not in {"done", "failed", "cancelled"}:
            run["status"] = "cancelled"
        return RunStatus(
            run_id=run_id,
            status=run["status"],
            metadata={},
        )

    async def collect(self, run_id: str) -> RunResult | None:
        """Collect the run result if it exists."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        self._mark_collected(run_id)
        # Only release the idempotency key once the run has reached a terminal
        # state. Collecting an active run (e.g., an early/speculative poll)
        # must not break dedup for an in-flight execution (#353).
        if run["status"] in _TERMINAL_STATUSES:
            self._release_idempotency_key(run_id)
        return RunResult(
            run_id=run_id,
            status=run["status"],
            deliverables=[],
            metadata=run.get("result") or {},
        )

    async def add_feedback(
        self, run_id: str, feedback: FeedbackRequest
    ) -> RunStatus | None:
        """Append Controller feedback to a run.

        Refuse feedback for runs that have already reached a terminal state
        (#246).
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        if run["status"] in _TERMINAL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run {run_id} is already in terminal state {run['status']}",
            )
        run.setdefault("feedback", []).append(feedback.model_dump(mode="json"))
        return RunStatus(
            run_id=run_id,
            status=run["status"],
            metadata={"feedback_count": len(run["feedback"])},
        )

    def snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        """Return a read-only snapshot of stored runs."""
        return dict(self._runs)


# Module-level singleton used by the FastAPI app.
store = RunStore()
