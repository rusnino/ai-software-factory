"""Task and project profile persistence service."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select

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
        existing = await self.db.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == project_profile.project_id
            )
        )
        if existing is None:
            self.db.add(
                ProjectProfileModel(
                    project_id=project_profile.project_id,
                    profile_json=project_profile_json,
                )
            )
            await AuditService.log(
                db=self.db,
                event_type="project_profile_created",
                task_id=task_contract.task_id,
                actor=task_contract.proposed_by,
                source="task_service",
                payload={
                    "project_id": project_profile.project_id,
                    "profile_version": project_profile.profile_version,
                },
            )
        else:
            existing.profile_json = project_profile_json
            await AuditService.log(
                db=self.db,
                event_type="project_profile_updated",
                task_id=task_contract.task_id,
                actor=task_contract.proposed_by,
                source="task_service",
                payload={
                    "project_id": project_profile.project_id,
                    "profile_version": project_profile.profile_version,
                },
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
        return await self.db.scalar(select(Task).where(Task.id == task_id))

    async def get_profile_by_project_id(
        self, project_id: str
    ) -> ProjectProfile | None:
        """Return the stored project profile for a project, or None."""
        row = await self.db.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == project_id
            )
        )
        if row is None:
            return None
        return ProjectProfile(**row.profile_json)
