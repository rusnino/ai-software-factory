from typing import Any

from pydantic import BaseModel


class ExecutionConfig(BaseModel):
    allowed_harnesses: list[str] = ["opencode"]
    sandbox: str = "worktree"
    timeout_minutes: int = 60
    max_parallel_agents: int = 3


class RepositoryConfig(BaseModel):
    path: str
    default_branch: str = "main"


class SecurityConfig(BaseModel):
    forbidden_paths: list[str] = []
    docker_socket: str = "deny"
    network: str = "restricted"
    destructive_shell: str = "deny"
    spawn_subagents: str = "deny"


class GitConfig(BaseModel):
    force_push: str = "deny"
    merge_requires_human: bool = True
    signed_commits: str = "optional"


class ProjectProfile(BaseModel):
    profile_version: str = "1.0"
    project_id: str
    project_name: str
    repository: RepositoryConfig
    security: SecurityConfig = SecurityConfig()
    git: GitConfig = GitConfig()
    execution: ExecutionConfig = ExecutionConfig()
    llm: dict[str, Any] = {}
    audit: dict[str, Any] = {}
