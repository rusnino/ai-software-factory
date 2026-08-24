"""Terminal alerting and verification failure feedback service."""

from __future__ import annotations

from typing import Any

import structlog

from governance_controller.adapters.plane_client import PlaneClient
from governance_controller.config import settings
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
        """
        summary = _format_verification_failure_report(
            task, contract, report, attempt, max_retries
        )
        logger.warning(
            "verification_failure_feedback",
            task_id=task.id,
            attempt=attempt,
            max_retries=max_retries,
            summary=summary,
        )
        await self._plane_comment(task.id, summary)

    async def notify_terminal_failure(
        self,
        task: Task,
        contract: TaskContract,
        report: dict[str, Any],
        reason: str,
    ) -> None:
        """Alert humans when a task reaches a terminal failed/blocked state."""
        summary = (
            f"Terminal failure for task {task.id} ({contract.objective}).\n"
            f"Reason: {reason}\n"
            f"State: {task.state.value}\n"
            f"Verification report:\n{_format_report(report)}"
        )
        logger.error(
            "terminal_failure_alert",
            task_id=task.id,
            state=task.state.value,
            reason=reason,
            summary=summary,
        )
        await self._plane_comment(task.id, summary)
        # Future: send Telegram/Email/SMS here.

    async def _plane_comment(
        self,
        plane_issue_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        client = self._plane()
        if client is None:
            return None
        try:
            return await client.add_comment(plane_issue_id, text)
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
            lines.append(f"FAILED: {check.get('name')}")
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
