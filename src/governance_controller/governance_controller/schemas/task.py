"""Task API schemas."""

from datetime import datetime

from pydantic import BaseModel

from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract


class TaskCreateRequest(BaseModel):
    """Payload for creating a new governed task."""

    task_contract: TaskContract
    project_profile: ProjectProfile


class TaskResponse(BaseModel):
    """API response containing task identity and current state."""

    id: str
    state: str
    project_id: str
    created_at: datetime
    updated_at: datetime | None
