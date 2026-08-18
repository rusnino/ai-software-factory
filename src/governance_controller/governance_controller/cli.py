"""Command-line interface for Governance Controller approvals."""

from datetime import UTC, datetime

import httpx
import typer

from governance_controller.constants import ApprovalType
from governance_controller.schemas.approval import ApprovalRequest

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
