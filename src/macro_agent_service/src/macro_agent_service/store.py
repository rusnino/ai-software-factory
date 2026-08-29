"""In-memory run store for the scaffold."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

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

    def _evict_if_needed(self) -> None:
        """Drop oldest terminal runs when the store reaches its cap.

        Raises HTTPException when no terminal runs can be evicted and the cap
        is still exceeded.
        """
        while len(self._runs) >= self._max_runs:
            terminal_keys = [
                run_id
                for run_id, run in self._runs.items()
                if run["status"] in _TERMINAL_STATUSES
            ]
            if not terminal_keys:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Run store capacity exhausted; no terminal runs to evict",
                )
            oldest = min(terminal_keys, key=lambda k: self._runs[k]["created_at"])
            del self._runs[oldest]

    async def create(self, request: RunRequest) -> RunResponse:
        """Create a new run and return its handle."""
        import time

        self._evict_if_needed()
        run_id = str(uuid.uuid4())
        self._runs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "request": request.model_dump(),
            "result": None,
            "created_at": time.monotonic(),
        }
        return RunResponse(run_id=run_id, status="queued")

    async def get(self, run_id: str) -> RunStatus | None:
        """Return the run status, or None if not found."""
        run = self._runs.get(run_id)
        if run is None:
            return None
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
