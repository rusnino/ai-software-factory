"""Pydantic models for the macro-agent service API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

SandboxMode = Literal["worktree", "docker", "firecracker", "kata"]


class RunRequest(BaseModel):
    """Payload to start a macro-agent run."""

    task_id: str
    team: str = "default"
    harness: str = "opencode"
    role: str = "worker"
    objective: str
    acceptance: list[str] = Field(default_factory=list)
    timeout_minutes: int = 60
    max_retries: int = 2
    sandbox: SandboxMode = "worktree"
    max_parallel_agents: int = 3
    uses_docker_socket: bool = False
    destructive_shell: bool = False
    spawn_subagents: bool = False
    network_access: str = "restricted"
    force_push: bool = False
    signed_commits: bool = False
    opentasks_dag: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResponse(BaseModel):
    """Response after starting a run."""

    run_id: str
    status: str


class RunStatus(BaseModel):
    """Macro-agent run status."""

    run_id: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Result collected from a macro-agent run."""

    run_id: str
    status: str
    deliverables: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    """Failure feedback from the Controller to a running macro-agent run."""

    controller_task_id: str
    controller_state: str
    verification_report: dict[str, Any]
    execution_attempts: int
    max_retries: int
    objective: str
    acceptance: list[str] = Field(default_factory=list)
