from typing import Any

from pydantic import BaseModel

from governance_controller.schemas.completion_contract import CompletionContract


class ExecutionConfig(BaseModel):
    """Task-level execution request.

    NOTE: this is distinct from ``ProjectProfile``'s
    ``ProjectExecutionConfig``, which describes project-level constraints.
    """

    team: str = "default"
    harness: str = "opencode"
    role: str = "worker"
    timeout_minutes: int = 60
    max_retries: int = 2
    uses_docker_socket: bool = False
    destructive_shell: bool = False
    spawn_subagents: bool = False
    network_access: str = "restricted"  # "restricted" or "unrestricted"
    force_push: bool = False
    signed_commits: bool = False


class TaskContract(BaseModel):
    contract_version: str = "1.0"
    task_id: str
    project_id: str
    proposed_by: str
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
    completion_contract: CompletionContract | None = None
    opentasks_dag: dict[str, Any] | None = None
