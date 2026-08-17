from typing import Any

from pydantic import BaseModel


class ExecutionConfig(BaseModel):
    team: str = "default"
    harness: str = "opencode"
    timeout_minutes: int = 60
    max_retries: int = 2


class TaskContract(BaseModel):
    contract_version: str = "1.0"
    task_id: str
    project_id: str
    objective: str
    inputs: list[str] = []
    dependencies: list[str] = []
    constraints: list[str] = []
    acceptance: list[str]
    deliverables: list[str] = []
    execution: ExecutionConfig = ExecutionConfig()
    verification: dict[str, Any] = {}
    forbidden_paths: list[str] = []
    approval_required: bool = True
