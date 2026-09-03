"""Command-line interface for Governance Controller approvals."""

import asyncio
from datetime import UTC, datetime

import httpx
import typer
from sqlalchemy import select

from governance_controller.config import settings
from governance_controller.constants import ApprovalType
from governance_controller.db import dispose_engines_sync, get_db_session
from governance_controller.models.audit_log import AuditLog
from governance_controller.models.task import Task
from governance_controller.schemas.approval import ApprovalRequest
from governance_controller.services.audit_service import AuditService
from governance_controller.services.plane_projection import (
    acquire_plane_projection_lock,
)
from governance_controller.services.reconciliation_service import (
    ReconciliationService,
)
from governance_controller.services.stuck_execution_poller import (
    StuckExecutionPoller,
)
from governance_controller.services.task_service import (
    PLANE_PROJECTION_SOURCE_METADATA_KEY,
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
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help="Optional Idempotency-Key header value for safe retries.",
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
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
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


def _verify_audit_chain(rows: list[AuditLog]) -> tuple[int, str]:
    """Walk the AuditLog hash chain and return (exit_code, message)."""
    if not rows:
        return 0, "No audit log rows to verify."

    legacy_boundary_id: int | None = None
    previous_row_hash = ""

    for index, row in enumerate(rows):
        if row.row_hash == "" or row.row_hash is None:
            # Unverifiable legacy row (pre-hash schema). Mark the boundary at
            # the last such row before the first verifiable entry.
            legacy_boundary_id = row.id
            continue

        if previous_row_hash == "" and index > 0:
            # First verifiable row after a legacy prefix: the previous row is
            # the explicit boundary.
            legacy_boundary_id = rows[index - 1].id

        if row.previous_hash != previous_row_hash:
            return (
                1,
                f"Audit hash chain broken at AuditLog id={row.id} "
                "(previous_hash does not match prior row_hash).",
            )

        computed = row.compute_hash()
        if computed != row.row_hash:
            return (
                1,
                f"Audit hash chain broken at AuditLog id={row.id} "
                "(row_hash does not match computed hash).",
            )

        previous_row_hash = row.row_hash

    parts = ["Audit hash chain verified."]
    if legacy_boundary_id is not None:
        parts.append(
            f"Legacy boundary at AuditLog id={legacy_boundary_id}; "
            "rows at or before this point are unverifiable."
        )
    return 0, " ".join(parts)


@app.command()
def verify_audit() -> None:
    """Walk the AuditLog hash chain and report the first mismatch.

    Legacy rows that predate the hash-chain columns are reported as an
    explicit unverifiable boundary rather than a chain break.
    """

    async def _run() -> int:
        async with get_db_session() as db:
            result = await db.execute(
                select(AuditLog).order_by(AuditLog.id)
            )
            rows = list(result.scalars().all())
            exit_code, message = _verify_audit_chain(rows)
            typer.echo(message)
            return exit_code

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        exit_code = loop.run_until_complete(_run())
    finally:
        dispose_engines_sync(loop)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    raise typer.Exit(code=exit_code)


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
            retry_task_ids = [
                row.id
                for row in rows
                if row.plane_issue_id is None and settings.plane_base_url
            ]
            for task_id in retry_task_ids:
                task = await db.get(Task, task_id)
                if task is None:
                    continue
                task_project_id = task.project_id
                contract = task.task_contract_json
                objective: str | None = None
                description: str | None = None
                source: str | None = None
                approval_required: bool | None = None
                if isinstance(contract, dict):
                    obj = contract.get("objective")
                    if isinstance(obj, str):
                        objective = obj
                    acc = contract.get("acceptance")
                    if isinstance(acc, list) and acc and isinstance(acc[0], str):
                        description = acc[0]
                    stored_source = contract.get(PLANE_PROJECTION_SOURCE_METADATA_KEY)
                    if isinstance(stored_source, str) and stored_source:
                        source = stored_source
                    stored_approval_required = contract.get("approval_required")
                    if isinstance(stored_approval_required, bool):
                        approval_required = stored_approval_required
                title = objective or task_id
                pending_event_id: str | None = None
                try:
                    # Keep the task-scoped lock out of the read transaction and
                    # release it immediately after the non-authoritative write.
                    pending = await AuditService.log(
                        db=db,
                        event_type="plane_issue_creation_pending",
                        task_id=task_id,
                        actor="system",
                        source="cli",
                        payload={
                            "operation": "create_issue",
                            "project_id": task_project_id,
                        },
                    )
                    pending_event_id = pending.event_id
                    await db.commit()
                    await acquire_plane_projection_lock(db, task_id)
                    issue = await projection.ensure_plane_issue(
                        controller_task_id=task_id,
                        title=title,
                        description=description,
                        state=task.state,
                        project_id=task_project_id,
                        source=source,
                        approval_required=approval_required,
                    )
                    if isinstance(issue, dict):
                        plane_issue_id = issue.get("id")
                        if isinstance(plane_issue_id, str):
                            task.plane_issue_id = plane_issue_id
                            await db.flush()
                    await AuditService.log(
                        db=db,
                        event_type="plane_issue_creation_completed",
                        task_id=task_id,
                        actor="system",
                        source="cli",
                        payload={
                            "operation": "create_issue",
                            "pending_event_id": pending_event_id,
                            "plane_issue_id": task.plane_issue_id,
                        },
                    )
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    await AuditService.log(
                        db=db,
                        event_type="plane_issue_creation_failed",
                        task_id=task_id,
                        actor="system",
                        source="cli",
                        payload={
                            "operation": "create_issue",
                            "project_id": task_project_id,
                            "pending_event_id": pending_event_id,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )
                    await db.commit()
                    typer.echo(
                        f"[retry] plane issue creation failed for {task_id}: {exc}",
                        err=True,
                    )

            # Build the snapshot after retries so newly captured Plane issue IDs
            # are used by this reconciliation pass (#282). Re-reading also
            # avoids using expired or stale ORM objects after a retry rollback.
            result = await db.execute(
                select(Task)
                .where(Task.project_id == project_id)  # type: ignore[arg-type]
                .execution_options(populate_existing=True)
            )
            rows = result.scalars().all()
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
