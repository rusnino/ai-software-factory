"""Regression tests for project-profile insert races (GAP-079)."""

import pytest
from sqlalchemy import select

from governance_controller.models.project_profile import ProjectProfileModel
from governance_controller.schemas import ProjectProfile, RepositoryConfig, TaskContract
from governance_controller.services.task_service import TaskService


@pytest.fixture
def sample_contract() -> TaskContract:
    return TaskContract(
        task_id="race-task-1",
        project_id="race-proj-1",
        proposed_by="agent-1",
        objective="Race-safe profile insert",
        acceptance=["Profile race recovered"],
    )


@pytest.fixture
def sample_profile() -> ProjectProfile:
    return ProjectProfile(
        project_id="race-proj-1",
        project_name="Race Project",
        repository=RepositoryConfig(path="/tmp/repo"),
    )


class TestTaskServiceProfileRace:
    async def test_profile_insert_race_is_recovered(
        self,
        db_session,
        sample_contract: TaskContract,
        sample_profile: ProjectProfile,
    ) -> None:
        """If another transaction created the profile first, create() still succeeds."""
        # Pre-seed the profile as if a concurrent request won the race.
        db_session.add(
            ProjectProfileModel(
                project_id=sample_profile.project_id,
                profile_json=sample_profile.model_dump(mode="json"),
            )
        )
        await db_session.commit()

        service = TaskService(db_session)
        task = await service.create(
            task_contract=sample_contract,
            project_profile=sample_profile,
        )

        assert task.id == sample_contract.task_id
        assert task.project_id == sample_contract.project_id

        # A single profile row exists.
        row = await db_session.scalar(
            select(ProjectProfileModel).where(
                ProjectProfileModel.project_id == sample_profile.project_id
            )
        )
        assert row is not None
