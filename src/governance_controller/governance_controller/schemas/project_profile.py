from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from governance_controller.utils.bounded_dict import bounded_dict_field
from governance_controller.utils.git_ref import is_valid_git_branch_name
from governance_controller.utils.paths import reject_root_prefixes

SandboxMode = Literal["worktree", "docker", "firecracker", "kata"]


class ProjectExecutionConfig(BaseModel):
    """Project-level execution constraints.

    NOTE: this is distinct from ``TaskContract``'s ``ExecutionConfig``,
    which describes the execution request for a single task.
    """

    allowed_harnesses: list[str] = ["opencode"]
    sandbox: SandboxMode = "worktree"
    timeout_minutes: int = Field(default=60, ge=0)
    max_retries: int = Field(default=2, ge=0)
    max_parallel_agents: int = Field(default=3, ge=0)


class RepositoryConfig(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    default_branch: str = Field(default="main", min_length=1, max_length=256)

    @field_validator("default_branch")
    @classmethod
    def _validate_default_branch(cls, value: str) -> str:
        """Reject anything that isn't a safe git ref-name argv token.

        ``default_branch`` is interpolated directly into a `git diff` argv
        position (``VerificationService._git_committed_touched_paths``). A
        value starting with `-` would be parsed by git as a command-line
        flag rather than a revision, letting an ordinary task proposer
        inject arbitrary git options (NEXT-37 / GH #418).
        """
        if not is_valid_git_branch_name(value):
            raise ValueError(
                f"default_branch is not a valid git branch name: {value!r}"
            )
        return value


class SecurityConfig(BaseModel):
    forbidden_paths: list[str] = []
    _validate_forbidden_paths = reject_root_prefixes("forbidden_paths")

    docker_socket: Literal["allow", "deny"] = "deny"
    network: Literal["restricted", "unrestricted"] = "restricted"
    destructive_shell: Literal["allow", "deny"] = "deny"
    spawn_subagents: Literal["allow", "deny"] = "deny"


class GitConfig(BaseModel):
    force_push: Literal["allow", "deny"] = "deny"
    merge_requires_human: bool = True
    signed_commits: Literal["optional", "required"] = "optional"


class ProjectProfile(BaseModel):
    profile_version: str = "1.0"
    project_id: str = Field(..., min_length=1, max_length=128)
    project_name: str = Field(default="", max_length=256)
    repository: RepositoryConfig
    security: SecurityConfig = SecurityConfig()
    git: GitConfig = GitConfig()
    execution: ProjectExecutionConfig = ProjectExecutionConfig()
    llm: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    _validate_llm_bounded = bounded_dict_field("llm")
    _validate_audit_bounded = bounded_dict_field("audit")
