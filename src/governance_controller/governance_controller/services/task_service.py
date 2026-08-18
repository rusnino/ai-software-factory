"""Task and project profile persistence service."""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.models.task import Task
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.audit_service import AuditService


class TaskService:
    """Create and retrieve Task records with stored contracts and profiles."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        task_contract: TaskContract,
        project_profile: ProjectProfile,
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

        return task

    async def get_by_id(self, task_id: str) -> Task | None:
        """Return the Task with the given primary key, or None."""
        return await self.db.scalar(
            select(Task).where(Task.id == task_id)  # type: ignore[arg-type]
        )

    async def get_by_id_for_update(self, task_id: str) -> Task | None:
        """Return the Task with the given primary key, locked for update.

        .. deprecated::
            Prefer :meth:`get_by_id`. Approval concurrency is handled by
            ``StateMachine.atomic_transition()`` rather than a long-held row
            lock, so callers should not acquire ``FOR UPDATE`` locks.
        """
        result = await self.db.execute(
            select(Task).where(Task.id == task_id).with_for_update()  # type: ignore[arg-type]
        )
        return result.scalar_one_or_none()

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
            stmt = pg_insert(ProjectProfileModel).values(
                project_id=project_profile.project_id,
                profile_json=project_profile_json,
            ).on_conflict_do_nothing(index_elements=["project_id"])
        else:
            stmt = sqlite_insert(ProjectProfileModel).values(
                project_id=project_profile.project_id,
                profile_json=project_profile_json,
            ).on_conflict_do_nothing(index_elements=["project_id"])

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

    async def get_profile_by_project_id(
        self, project_id: str
    ) -> ProjectProfile | None:
        """Return the stored project profile for a project, or None."""
        row = await self.db.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == project_id  # type: ignore[arg-type]
            )
        )
        if row is None:
            return None
        return ProjectProfile(**cast(dict[str, Any], row.profile_json))
