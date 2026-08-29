"""Terminal alerting and verification failure feedback service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
from governance_controller.models.processed_event import ProcessedEvent
from governance_controller.models.task import Task
from governance_controller.schemas.task_contract import TaskContract

logger = structlog.get_logger(__name__)


class AlertService:
    """Emit terminal alerts and verification failure feedback.

    Phase 2 implementation writes Plane comments when Plane is configured and
    logs structured audit events. Future phases may add Telegram/Email/SMS
    channels.
    """

    def __init__(self, plane_client: PlaneClient | None = None) -> None:
        self._plane_client = plane_client

    def _plane(self) -> PlaneClient | None:
        if self._plane_client is not None:
            return self._plane_client
        if not settings.plane_base_url:
            return None
        return PlaneClient()

    async def notify_verification_failure(
        self,
        task: Task,
        contract: TaskContract,
        report: dict[str, Any],
        attempt: int,
        max_retries: int,
    ) -> None:
        """Notify humans about a verification failure.

        The notification is idempotent at the Plane-comment level by relying
        on the caller to invoke it once per verification failure event.
        Raw command stderr is kept in the audit log but is not posted to Plane,
        because Plane is a wider trust boundary than the Controller's own log.
        """
        public_report = _sanitize_report_for_plane(report)
        summary = _format_verification_failure_report(
            task, contract, public_report, attempt, max_retries
        )
        logger.warning(
            "verification_failure_feedback",
            task_id=task.id,
            attempt=attempt,
            max_retries=max_retries,
            summary=summary,
        )
        await self._plane_comment(
            task.plane_issue_id or task.id, summary, project_id=task.project_id
        )

    async def notify_terminal_failure(
        self,
        task: Task,
        contract: TaskContract,
        report: dict[str, Any],
        reason: str,
        db: AsyncSession | None = None,
    ) -> None:
        """Alert humans when a task reaches a terminal failed/blocked state.

        Alerts are idempotent: the same (task, reason) pair only sends one
        Plane comment even if invoked across retries or duplicate deliveries.
        """
        public_report = _sanitize_report_for_plane(report)
        summary = (
            f"Terminal failure for task {task.id} ({contract.objective}).\n"
            f"Reason: {reason}\n"
            f"State: {task.state.value}\n"
            f"Verification report:\n{_format_report(public_report)}"
        )
        logger.error(
            "terminal_failure_alert",
            task_id=task.id,
            state=task.state.value,
            reason=reason,
            summary=summary,
        )
        if db is not None and await self._already_alerted(
            db, task.id, "terminal_failure", reason
        ):
            logger.info(
                "terminal_failure_alert_skipped",
                task_id=task.id,
                reason=reason,
                detail="duplicate alert suppressed",
            )
            return
        await self._plane_comment(
            task.plane_issue_id or task.id, summary, project_id=task.project_id
        )
        if db is not None:
            await self._record_alert(
                db, task.id, "terminal_failure", reason
            )
        # Future: send Telegram/Email/SMS here.

    async def _already_alerted(
        self,
        db: AsyncSession,
        task_id: str,
        alert_type: str,
        reason: str,
    ) -> bool:
        event_id = f"{alert_type}:{reason}"
        row = await db.scalar(
            select(ProcessedEvent).where(
                ProcessedEvent.task_id == task_id,  # type: ignore[arg-type]
                ProcessedEvent.event_type == "alert",  # type: ignore[arg-type]
                ProcessedEvent.event_id == event_id,  # type: ignore[arg-type]
            )
        )
        return row is not None

    async def _record_alert(
        self,
        db: AsyncSession,
        task_id: str,
        alert_type: str,
        reason: str,
    ) -> None:
        db.add(
            ProcessedEvent(
                task_id=task_id,
                event_type="alert",
                event_id=f"{alert_type}:{reason}",
                event_timestamp=datetime.now(UTC),
            )
        )
        await db.commit()

    async def _plane_comment(
        self,
        plane_issue_id: str,
        text: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        client = self._plane()
        if client is None:
            return None
        try:
            return await client.add_comment(
                plane_issue_id, text, project_id=project_id
            )
        except Exception as exc:
            logger.warning(
                "plane_comment_failed",
                plane_issue_id=plane_issue_id,
                error=str(exc),
            )
            return None


def _format_verification_failure_report(
    task: Task,
    contract: TaskContract,
    report: dict[str, Any],
    attempt: int,
    max_retries: int,
) -> str:
    lines = [
        f"Verification failed for task {task.id} ({contract.objective}).",
        f"Attempt {attempt} of {max_retries}.",
        "",
    ]
    for check in report.get("checks", []):
        if check.get("status") == "failed":
            name = check.get("name", "")
            if name.startswith("optional:"):
                kind = "optional (informational)"
            else:
                kind = "required (blocking)"
            lines.append(f"FAILED ({kind}): {name}")
            lines.append(f"  command: {check.get('command', 'n/a')}")
            lines.append(f"  expected exit: {check.get('expected_exit', 'n/a')}")
            lines.append(f"  actual exit: {check.get('actual_exit', 'n/a')}")
            if check.get("stderr"):
                lines.append(f"  stderr: {check['stderr'][:500]}")
            if check.get("detail"):
                lines.append(f"  detail: {check['detail']}")
            lines.append("")
    return "\n".join(lines)


def _format_report(report: dict[str, Any]) -> str:
    pieces: list[str] = []
    for check in report.get("checks", []):
        if check.get("status") == "failed":
            pieces.append(
                f"- {check.get('name')}: {check.get('detail', check)}"
            )
    return "\n".join(pieces) if pieces else "No detailed checks available."


def _sanitize_report_for_plane(report: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the verification report safe to post to Plane.

    Raw stdout/stderr from verification commands can contain secrets, file
    paths, or other data that should not leave the Controller's audit log.
    This strips those fields from each check while preserving the names,
    statuses, exit codes, and non-secret detail.
    """
    sanitized = dict(report)
    checks: list[dict[str, Any]] = []
    for check in report.get("checks", []):
        safe = {
            key: value
            for key, value in check.items()
            if key not in {"stdout", "stderr"}
        }
        checks.append(safe)
    sanitized["checks"] = checks
    return sanitized
