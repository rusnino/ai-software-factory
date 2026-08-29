"""Task API schemas."""

from datetime import datetime

from pydantic import BaseModel, model_validator

from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract


class TaskCreateRequest(BaseModel):
    """Payload for creating a new governed task."""

    task_contract: TaskContract
    project_profile: ProjectProfile

    @model_validator(mode="after")
    def _project_ids_match(self) -> "TaskCreateRequest":
        """Reject cross-project profile poisoning (#244)."""
        contract_project = self.task_contract.project_id
        profile_project = self.project_profile.project_id
        if contract_project != profile_project:
            raise ValueError(
                "task_contract.project_id and project_profile.project_id must match: "
                f"{contract_project} != {profile_project}"
            )
        return self


class TaskResponse(BaseModel):
    """API response containing task identity and current state."""

    id: str
    state: str
    project_id: str
    proposed_by: str
    created_at: datetime
    updated_at: datetime | None
    execution_attempts: int
