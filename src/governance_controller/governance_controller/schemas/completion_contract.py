from pydantic import BaseModel, Field, field_validator


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


class ScopeCheck(BaseModel):
    description: str
    allowed_paths: list[str] = []
    forbidden_paths: list[str] = []

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def _reject_root_prefix_paths(cls, value: list[str]) -> list[str]:
        """Reject paths that normalize to the filesystem root.

        Such paths would match every touched path as an empty-string prefix and
        therefore break the scope/forbidden-path check intent.
        """
        from governance_controller.utils.paths import normalize_path

        for path in value:
            try:
                normalized = normalize_path(path)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if normalized == "/":
                raise ValueError(
                    f"path normalizes to root and cannot be used as a prefix: {path!r}"
                )
        return value


class CompletionContract(BaseModel):
    task_id: str
    required: list[Check] = []
    optional: list[Check] = []
    forbidden_path_check: ForbiddenPathCheck = ForbiddenPathCheck()
    scope_check: ScopeCheck
