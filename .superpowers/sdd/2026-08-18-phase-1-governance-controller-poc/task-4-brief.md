# Task 4 Brief: Task Contract / Project Profile / Completion Contract Schemas

**Goal:** Add Pydantic v2 schemas that match SPEC-03 §3.5–3.7 for Task Contract, Project Profile, and Completion Contract.

**Files to create:**
- `src/governance_controller/governance_controller/schemas/task_contract.py`
- `src/governance_controller/governance_controller/schemas/project_profile.py`
- `src/governance_controller/governance_controller/schemas/completion_contract.py`
- `src/governance_controller/tests/test_schemas.py`

**Files to modify:**
- `src/governance_controller/governance_controller/schemas/__init__.py` (re-export)

**Exact values to use verbatim:**

TaskContract fields:
- `contract_version: str = "1.0"`
- `task_id: str`
- `project_id: str`
- `objective: str`
- `inputs: list[str] = []`
- `dependencies: list[str] = []`
- `constraints: list[str] = []`
- `acceptance: list[str]`
- `deliverables: list[str] = []`
- `execution: ExecutionConfig` with fields:
  - `team: str = "default"`
  - `harness: str = "opencode"`
  - `timeout_minutes: int = 60`
  - `max_retries: int = 2`
- `verification: dict[str, Any] = {}`
- `forbidden_paths: list[str] = []`
- `approval_required: bool = True`

ProjectProfile fields:
- `profile_version: str = "1.0"`
- `project_id: str`
- `project_name: str`
- `repository: RepositoryConfig` with fields:
  - `path: str`
  - `default_branch: str = "main"`
- `security: SecurityConfig` with defaults:
  - `forbidden_paths: list[str] = []`
  - `docker_socket: str = "deny"`
  - `network: str = "restricted"`
  - `destructive_shell: str = "deny"`
  - `spawn_subagents: str = "deny"`
- `git: GitConfig` with defaults:
  - `force_push: str = "deny"`
  - `merge_requires_human: bool = True`
  - `signed_commits: str = "optional"`
- `execution: ExecutionConfig` with defaults:
  - `allowed_harnesses: list[str] = ["opencode"]`
  - `sandbox: str = "worktree"`
  - `timeout_minutes: int = 60`
  - `max_parallel_agents: int = 3`
- `llm: dict[str, Any] = {}`
- `audit: dict[str, Any] = {}`

CompletionContract fields:
- `task_id: str`
- `required: list[Check] = []` where `Check` has:
  - `type: str`
  - `command: str`
  - `expect_exit: int = 0`
- `optional: list[Check] = []`
- `forbidden_path_check: ForbiddenPathCheck` with:
  - `paths: list[str] = []`
- `scope_check: ScopeCheck` with:
  - `description: str`
  - `allowed_paths: list[str] = []`
  - `forbidden_paths: list[str] = []`

**Interfaces produced:**
- `TaskContract`, `ExecutionConfig`
- `ProjectProfile`, `RepositoryConfig`, `SecurityConfig`, `GitConfig`
- `CompletionContract`, `Check`, `ForbiddenPathCheck`, `ScopeCheck`

**Constraints:**
- Pydantic v2 BaseModel.
- Default values must match the verbatim values above.
- Schemas must be importable from `governance_controller.schemas`.

**Verification steps:**
1. Write `tests/test_schemas.py` covering:
   - TaskContract construction with all required fields.
   - ProjectProfile defaults (allowed_harnesses=["opencode"], docker_socket="deny").
   - CompletionContract scope_check field.
2. Run `uv run pytest tests/test_schemas.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add TaskContract, ProjectProfile, CompletionContract schemas`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-4-report.md`
