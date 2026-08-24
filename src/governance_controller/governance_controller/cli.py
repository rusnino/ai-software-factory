"""Command-line interface for Governance Controller approvals."""

import asyncio
from datetime import UTC, datetime

import httpx
import typer

from governance_controller.constants import ApprovalType
from governance_controller.schemas.approval import ApprovalRequest
from governance_controller.services.reconciliation_service import (
    ReconciliationService,
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

    try:
        response = httpx.post(
            f"{base_url}/approvals",
            json=payload.model_dump(mode="json"),
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
) -> None:
    """List Plane vs Controller divergences for a project.

    This command fetches Plane issues and compares their execution state with
    the authoritative Controller state. It prints divergences to stdout and
    exits with code 1 when serious drift is detected.
    """
    from governance_controller import config

    if plane_base_url:
        config.settings.plane_base_url = plane_base_url

    async def _run() -> None:
        service = ReconciliationService()
        # For the CLI scaffold we have no live Controller DB connection, so we
        # report that Plane is reachable and surface configuration. A future
        # iteration will read task rows from the database.
        report = await service.reconcile(controller_tasks=[], project_id=project_id)
        typer.echo(f"Checked {report.checked} tasks")
        for div in report.divergences:
            typer.echo(
                f"[{div.severity}] {div.controller_task_id or div.plane_task_id} "
                f"{div.field}: {div.message}",
                err=div.severity == "alert",
            )
        if any(d.severity == "alert" for d in report.divergences):
            raise typer.Exit(code=1)

    asyncio.run(_run())
