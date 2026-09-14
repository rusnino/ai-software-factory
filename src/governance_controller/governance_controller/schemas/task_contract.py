import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from governance_controller.schemas.completion_contract import CompletionContract
from governance_controller.utils.bounded_dict import bounded_dict_field
from governance_controller.utils.paths import reject_root_prefixes


class ExecutionConfig(BaseModel):
    """Task-level execution request.

    NOTE: this is distinct from ``ProjectProfile``'s
    ``ProjectExecutionConfig``, which describes project-level constraints.
    """

    team: str = "default"
    harness: str = "opencode"
    role: str = "worker"
    timeout_minutes: int = Field(default=60, ge=1)
    max_retries: int = Field(default=2, ge=0)
    uses_docker_socket: bool = False
    destructive_shell: bool = False
    spawn_subagents: bool = False
    network_access: Literal["restricted", "unrestricted"] = "restricted"
    force_push: bool = False
    signed_commits: bool = False


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TaskContract(BaseModel):
    contract_version: str = "1.0"
    task_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    proposed_by: str = Field(..., min_length=1, max_length=128)
    objective: str = Field(..., min_length=1, max_length=8192)
    inputs: list[str] = Field(default=[], max_length=1000)
    dependencies: list[str] = Field(default=[], max_length=1000)
    constraints: list[str] = Field(default=[], max_length=1000)
    acceptance: list[str] = Field(..., min_length=1, max_length=1000)
    deliverables: list[str] = Field(default=[], max_length=1000)
    execution: ExecutionConfig = ExecutionConfig()
    verification: dict[str, Any] = {}
    forbidden_paths: list[str] = Field(default=[], max_length=1000)
    _validate_forbidden_paths = reject_root_prefixes("forbidden_paths")
    _validate_verification_bounded = bounded_dict_field("verification")

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        """Reject path-traversal and other unsafe characters in task_id (#255)."""
        if not _TASK_ID_RE.match(value):
            raise ValueError(
                "task_id may only contain letters, digits, underscores, "
                "hyphens, dots, and must not contain path separators"
            )
        return value

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, value: str) -> str:
        """Reject empty/whitespace-only objectives that bypass policy checks."""
        if not value.strip():
            raise ValueError("objective is empty or whitespace-only")
        return value

    @field_validator("proposed_by")
    @classmethod
    def _validate_proposed_by(cls, value: str) -> str:
        """Strip zero-width/control characters that could evade the
        self-approval guard's actor comparison (#376), mirroring the
        normalization ``PermissionService._normalize_actor`` already applies
        to the approving actor. ``proposed_by`` otherwise remains a free-form,
        unauthenticated identity -- see ``PermissionService.known_proposers``
        for the closed-universe mitigation this Controller offers today.
        """
        cleaned = "".join(ch for ch in value if ch.isprintable() or ch.isspace())
        if not cleaned.strip():
            raise ValueError("proposed_by is empty or whitespace-only")
        return cleaned

    @field_validator(
        "inputs",
        "dependencies",
        "constraints",
        "acceptance",
        "deliverables",
        "forbidden_paths",
    )
    @classmethod
    def _validate_string_list_items(cls, value: list[str]) -> list[str]:
        """Bound individual string items to keep audit logs and shell args safe."""
        for item in value:
            if not item.strip():
                raise ValueError("list item cannot be empty or whitespace-only")
            if len(item) > 4096:
                raise ValueError("list item exceeds maximum length of 4096 characters")
        return value

    approval_required: bool = True
    completion_contract: CompletionContract | None = None
    opentasks_dag: dict[str, Any] | None = None
    _validate_opentasks_dag_bounded = bounded_dict_field("opentasks_dag")
