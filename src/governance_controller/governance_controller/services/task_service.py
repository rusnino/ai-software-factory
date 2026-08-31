"""Task and project profile persistence service."""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.config import settings
from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService
from governance_controller.services.plane_projection import (
    PlaneProjectionService,
    acquire_plane_projection_lock,
)


class TaskService:
    """Create and retrieve Task records with stored contracts and profiles."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        task_contract: TaskContract,
        project_profile: ProjectProfile,
        source: str = "api",
    ) -> Task:
        """Create a new PROPOSED task and upsert its project profile.

        Args:
            task_contract: Validated task contract used for the task.
            project_profile: Validated project profile used for policy.

        Returns:
            The persisted Task record.
        """
        project_profile_json = project_profile.model_dump(mode="json")
        await self._upsert_project_profile(
            project_profile=project_profile,
            project_profile_json=project_profile_json,
            task_id=task_contract.task_id,
            actor=task_contract.proposed_by,
        )

        task = Task(
            id=task_contract.task_id,
            project_id=task_contract.project_id,
            proposed_by=task_contract.proposed_by,
            task_contract_json=task_contract.model_dump(mode="json"),
        )
        self.db.add(task)
        await self.db.flush()

        await AuditService.log(
            db=self.db,
            event_type="task_created",
            task_id=task.id,
            actor=task.proposed_by,
            source="task_service",
            payload={"project_id": task.project_id},
        )

        if settings.plane_base_url:
            # Commit the authoritative task and audit rows before the
            # non-authoritative Plane request can block on external I/O.
            await self.db.commit()
            await acquire_plane_projection_lock(self.db, task.id)
            try:
                issue = await PlaneProjectionService().ensure_plane_issue(
                    controller_task_id=task.id,
                    title=task_contract.objective or task.id,
                    description=task_contract.acceptance[0]
                    if task_contract.acceptance
                    else None,
                    state=task.state,
                    project_id=task.project_id,
                    source=source,
                    approval_required=task_contract.approval_required,
                )
                if isinstance(issue, dict):
                    plane_issue_id = issue.get("id")
                    if isinstance(plane_issue_id, str):
                        task.plane_issue_id = plane_issue_id
                        await self.db.flush()
                # Persist the Plane link before returning. A caller rollback or
                # crash after the Plane request must not lose the id that makes
                # subsequent ensure calls idempotent.
                await self.db.commit()
            except Exception as exc:
                # Plane projection failures must not block task creation, but
                # they must be durable and actionable. Record an audit entry so
                # reconciliation can retry later.
                await AuditService.log(
                    db=self.db,
                    event_type="plane_issue_creation_failed",
                    task_id=task.id,
                    actor="system",
                    source="task_service",
                    payload={
                        "project_id": task.project_id,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                await self.db.commit()

        return task

    async def get_by_id(self, task_id: str) -> Task | None:
        """Return the Task with the given primary key, or None."""
        return await self.db.scalar(
            select(Task).where(Task.id == task_id)  # type: ignore[arg-type]
        )

    async def get_by_plane_issue_id(self, plane_issue_id: str) -> Task | None:
        """Return the Task linked to the given Plane issue UUID, or None."""
        return await self.db.scalar(
            select(Task).where(
                Task.plane_issue_id == plane_issue_id  # type: ignore[arg-type]
            )
        )

    async def _upsert_project_profile(
        self,
        project_profile: ProjectProfile,
        project_profile_json: dict[str, Any],
        task_id: str,
        actor: str,
    ) -> None:
        """Insert or update a project profile, tolerating insert races.

        Two concurrent task creations for the same new project could both see
        no profile. The loser gets an insert conflict, which we ignore with
        dialect-specific ``ON CONFLICT DO NOTHING`` / ``INSERT OR IGNORE``,
        then refetch and update if the stored profile differs. We never roll
        back here because that would undo earlier work in the same transaction.
        """
        dialect = self.db.bind.dialect.name if self.db.bind else "sqlite"
        stmt: Any
        if dialect == "postgresql":
            stmt = (
                pg_insert(ProjectProfileModel)
                .values(
                    project_id=project_profile.project_id,
                    profile_json=project_profile_json,
                )
                .on_conflict_do_nothing(index_elements=["project_id"])
            )
        else:
            stmt = (
                sqlite_insert(ProjectProfileModel)
                .values(
                    project_id=project_profile.project_id,
                    profile_json=project_profile_json,
                )
                .on_conflict_do_nothing(index_elements=["project_id"])
            )

        result = await self.db.execute(stmt)
        did_insert = bool(getattr(result, "rowcount", 1))

        existing = await self.db.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == project_profile.project_id  # type: ignore[arg-type]
            )
        )
        if existing is None:  # pragma: no cover - unreachable on sane DB
            raise RuntimeError(
                f"Project profile {project_profile.project_id} not available "
                "after upsert"
            )

        if existing.profile_json != project_profile_json:
            existing.profile_json = project_profile_json
            await AuditService.log(
                db=self.db,
                event_type="project_profile_updated",
                task_id=task_id,
                actor=actor,
                source="task_service",
                payload={
                    "project_id": project_profile.project_id,
                    "profile_version": project_profile.profile_version,
                },
            )
        elif did_insert:
            await AuditService.log(
                db=self.db,
                event_type="project_profile_created",
                task_id=task_id,
                actor=actor,
                source="task_service",
                payload={
                    "project_id": project_profile.project_id,
                    "profile_version": project_profile.profile_version,
                },
            )

    async def get_profile_by_project_id(self, project_id: str) -> ProjectProfile | None:
        """Return the stored project profile for a project, or None."""
        row = await self.db.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == project_id  # type: ignore[arg-type]
            )
        )
        if row is None:
            return None
        return ProjectProfile(**cast(dict[str, Any], row.profile_json))
