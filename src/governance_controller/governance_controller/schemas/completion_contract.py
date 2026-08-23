from pydantic import BaseModel, field_validator


class Check(BaseModel):
    type: str
    command: str
    expect_exit: int = 0

    @field_validator("command")
    @classmethod
    def _reject_control_characters(cls, value: str) -> str:
        """Reject embedded NUL bytes and other non-printable control chars."""
        if "\x00" in value:
            raise ValueError("command contains embedded NUL byte")
        if any(ord(ch) < 32 and ch not in {"\t", "\n"} for ch in value):
            raise ValueError("command contains disallowed control character")
        return value


class ForbiddenPathCheck(BaseModel):
    paths: list[str] = []


class ScopeCheck(BaseModel):
    description: str
    allowed_paths: list[str] = []
    forbidden_paths: list[str] = []


class CompletionContract(BaseModel):
    task_id: str
    required: list[Check] = []
    optional: list[Check] = []
    forbidden_path_check: ForbiddenPathCheck = ForbiddenPathCheck()
    scope_check: ScopeCheck
