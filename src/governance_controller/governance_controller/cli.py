"""Command-line interface for Governance Controller approvals."""

import asyncio
from datetime import UTC, datetime

import httpx
import typer
from sqlalchemy import select

from governance_controller.config import settings
from governance_controller.constants import ApprovalType
from governance_controller.db import dispose_engines_sync, get_db_session
from governance_controller.models.task import Task
from governance_controller.schemas.approval import ApprovalRequest
from governance_controller.services.reconciliation_service import (
    ReconciliationService,
)
from governance_controller.services.stuck_execution_poller import (
    StuckExecutionPoller,
)

app = typer.Typer(help="Governance Controller CLI")


@app.command()
def approve(
    task_id: str = typer.Argument(..., help="Task identifier to approve."),
    approval_type: ApprovalType = typer.Option(
        ApprovalType.EXECUTION.value,
        "--type",
        help="Type of approval to grant.",
    ),
    actor: str = typer.Option("cli-user", help="Actor granting the approval."),
    source: str = typer.Option("cli", help="Source channel of the approval."),
    base_url: str = typer.Option(
        "http://localhost:8000",
        help="Base URL of the Governance Controller API.",
    ),
    comment: str | None = typer.Option(
        None,
        help="Optional comment attached to the approval.",
    ),
    secret: str = typer.Option(
        settings.controller_api_secret,
        "--secret",
        envvar="GC_CONTROLLER_API_SECRET",
        help="X-Controller-Secret value for authenticated endpoints.",
    ),
) -> None:
    """Approve a task via the Controller's authoritative approvals endpoint."""
    payload = ApprovalRequest(
        task_id=task_id,
        approval_type=approval_type,
        source=source,
        actor=actor,
        timestamp=datetime.now(UTC).isoformat(),
        comment=comment,
    )

    headers = {"X-Controller-Secret": secret}
    try:
        response = httpx.post(
            f"{base_url}/approvals",
            json=payload.model_dump(mode="json"),
            headers=headers,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Approval failed: {exc.response.status_code}", err=True)
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        typer.echo(f"Approval failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    body = response.json()
    typer.echo(f"Approved {task_id}: {body['state']}")


@app.command()
def reconcile(
    project_id: str = typer.Argument(
        ..., help="Plane project UUID to reconcile."
    ),
    plane_base_url: str = typer.Option(
        "",
        help="Override GC_PLANE_BASE_URL for this run.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix/--apply",
        help="Apply state fixes back to Plane.",
    ),
) -> None:
    """List Plane vs Controller divergences for a project.

    This command fetches Plane issues, reads the authoritative Controller task
    states from the local database, compares them, and optionally applies state
    fixes back to Plane.
    """
    from governance_controller import config
    from governance_controller.services.plane_projection import (
        PlaneProjectionService,
    )

    if plane_base_url:
        config.settings.plane_base_url = plane_base_url

    async def _run() -> None:
        async with get_db_session() as db:
            result = await db.execute(
                select(Task).where(Task.project_id == project_id)  # type: ignore[arg-type]
            )
            rows = result.scalars().all()
            # Retry Plane issue creation for tasks that missed it at creation
            # time (#215). This keeps plane_issue_id in sync without blocking
            # the original task creation.
            projection = PlaneProjectionService()
            for row in rows:
                if row.plane_issue_id is not None:
                    continue
                if not settings.plane_base_url:
                    continue
                task = await db.get(Task, row.id)
                if task is None:
                    continue
                contract = task.task_contract_json
                objective: str | None = None
                description: str | None = None
                if isinstance(contract, dict):
                    obj = contract.get("objective")
                    if isinstance(obj, str):
                        objective = obj
                    acc = contract.get("acceptance")
                    if isinstance(acc, list) and acc and isinstance(acc[0], str):
                        description = acc[0]
                title = objective or task.id
                try:
                    issue = await projection.ensure_plane_issue(
                        controller_task_id=task.id,
                        title=title,
                        description=description,
                        state=task.state,
                        project_id=task.project_id,
                    )
                    if isinstance(issue, dict):
                        plane_issue_id = issue.get("id")
                        if isinstance(plane_issue_id, str):
                            task.plane_issue_id = plane_issue_id
                            await db.flush()
                except Exception as exc:
                    typer.echo(
                        f"[retry] plane issue creation failed for {task.id}: {exc}",
                        err=True,
                    )

            # Build the snapshot after retries so newly captured Plane issue IDs
            # are used by this reconciliation pass (#282).
            controller_tasks = [
                (
                    str(row.id),
                    row.state,
                    row.project_id or project_id,
                    row.plane_issue_id,
                )
                for row in rows
            ]

            service = ReconciliationService(db=db)
            report = await service.reconcile(
                controller_tasks=controller_tasks,
                project_id=project_id,
                fix=fix,
            )
        typer.echo(f"Checked {report.checked} tasks")
        for div in report.divergences:
            typer.echo(
                f"[{div.severity}] {div.controller_task_id or div.plane_task_id} "
                f"{div.field}: {div.message}",
                err=div.severity == "alert",
            )
        if any(d.severity == "alert" for d in report.divergences):
            raise typer.Exit(code=1)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
    finally:
        # Dispose the engine while the loop is still alive so pooled
        # connections close cleanly, then tear down the loop.
        dispose_engines_sync(loop)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


@app.command()
def poll_stuck_executions(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report stuck executions without marking them BLOCKED.",
    ),
) -> None:
    """Run one pass of the stuck-execution fallback poller.

    Scans RUNNING tasks whose latest execution has exceeded twice its configured
    timeout_minutes. When the macro-agent run is not still active, the task is
    moved to BLOCKED and an audit alert is recorded.
    """
    async def _run() -> None:
        async with get_db_session() as db:
            poller = StuckExecutionPoller(db, dry_run=dry_run)
            actions = await poller.poll()
            for action in actions:
                typer.echo(
                    f"{action['task_id']}: {action['action']} "
                    f"(deadline {action.get('deadline', 'n/a')})"
                )
            if not actions:
                typer.echo("No stuck executions detected")

    import structlog

    logger = structlog.get_logger("governance_controller.cli")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
    except Exception as exc:
        logger.error(
            "poll_stuck_executions_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            dry_run=dry_run,
        )
        raise typer.Exit(code=1) from exc
    finally:
        # Dispose the engine while the loop is still alive so pooled
        # connections close cleanly, then tear down the loop.
        dispose_engines_sync(loop)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    app()
