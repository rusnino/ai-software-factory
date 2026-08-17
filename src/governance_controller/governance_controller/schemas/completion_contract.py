from pydantic import BaseModel


class Check(BaseModel):
    type: str
    command: str
    expect_exit: int = 0


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
