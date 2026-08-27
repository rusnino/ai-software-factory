from pydantic import BaseModel, Field, field_validator

from governance_controller.utils.paths import reject_root_prefixes


class Check(BaseModel):
    type: str = Field(..., min_length=1, max_length=128)
    command: str = Field(..., min_length=1, max_length=4096)
    expect_exit: int = 0

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        """Reject empty/whitespace-only commands and disallowed control chars."""
        if "\x00" in value:
            raise ValueError("command contains embedded NUL byte")
        if any(ord(ch) < 32 and ch not in {"\t", "\n"} for ch in value):
            raise ValueError("command contains disallowed control character")
        stripped = value.strip()
        if not stripped:
            raise ValueError("command is empty or whitespace-only")
        return value


class ForbiddenPathCheck(BaseModel):
    paths: list[str] = []

    _validate_paths = reject_root_prefixes("paths")


class ScopeCheck(BaseModel):
    description: str
    allowed_paths: list[str] = []
    forbidden_paths: list[str] = []

    _validate_allowed_paths = reject_root_prefixes("allowed_paths")
    _validate_forbidden_paths = reject_root_prefixes("forbidden_paths")


class CompletionContract(BaseModel):
    task_id: str
    required: list[Check] = []
    optional: list[Check] = []
    forbidden_path_check: ForbiddenPathCheck = ForbiddenPathCheck()
    scope_check: ScopeCheck
