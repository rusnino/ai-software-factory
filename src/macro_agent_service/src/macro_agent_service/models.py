"""Pydantic models for the macro-agent service API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

SandboxMode = Literal["worktree", "docker", "firecracker", "kata"]


class RunRequest(BaseModel):
    """Payload to start a macro-agent run."""

    task_id: str = Field(..., min_length=1, max_length=256)
    team: str = Field(default="default", min_length=1, max_length=128)
    harness: str = Field(default="opencode", min_length=1, max_length=128)
    role: str = Field(default="worker", min_length=1, max_length=128)
    objective: str = Field(..., min_length=1, max_length=65536)
    acceptance: list[str] = Field(default_factory=list, max_length=1000)
    timeout_minutes: int = Field(default=60, ge=1, le=10080)
    max_retries: int = Field(default=2, ge=0, le=100)
    sandbox: SandboxMode = "worktree"
    max_parallel_agents: int = Field(default=3, ge=1, le=1000)
    uses_docker_socket: bool = False
    destructive_shell: bool = False
    spawn_subagents: bool = False
    network_access: str = Field(default="restricted", min_length=1, max_length=64)
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
