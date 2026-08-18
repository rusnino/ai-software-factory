from governance_controller.schemas import (
    Check,
    CompletionContract,
    ExecutionConfig,
    ForbiddenPathCheck,
    ProjectProfile,
    RepositoryConfig,
    ScopeCheck,
    TaskContract,
)


def test_task_contract_construction() -> None:
    contract = TaskContract(
        task_id="task-1",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Implement the thing",
        acceptance=["It works"],
        inputs=["input.txt"],
        deliverables=["output.py"],
        execution=ExecutionConfig(
            team="backend", harness="opencode", timeout_minutes=30, max_retries=1
        ),
    )

    assert contract.task_id == "task-1"
    assert contract.project_id == "project-1"
    assert contract.proposed_by == "agent-1"
    assert contract.objective == "Implement the thing"
    assert contract.inputs == ["input.txt"]
    assert contract.dependencies == []
    assert contract.constraints == []
    assert contract.acceptance == ["It works"]
    assert contract.deliverables == ["output.py"]
    assert contract.execution.team == "backend"
    assert contract.execution.harness == "opencode"
    assert contract.execution.timeout_minutes == 30
    assert contract.execution.max_retries == 1
    assert contract.verification == {}
    assert contract.forbidden_paths == []
    assert contract.approval_required is True
    assert contract.contract_version == "1.0"


def test_project_profile_defaults() -> None:
    profile = ProjectProfile(
        project_id="project-1",
        project_name="Test Project",
        repository=RepositoryConfig(path="/repo"),
    )

    assert profile.profile_version == "1.0"
    assert profile.security.docker_socket == "deny"
    assert profile.security.network == "restricted"
    assert profile.security.destructive_shell == "deny"
    assert profile.security.spawn_subagents == "deny"
    assert profile.git.force_push == "deny"
    assert profile.git.merge_requires_human is True
    assert profile.git.signed_commits == "optional"
    assert profile.execution.allowed_harnesses == ["opencode"]
    assert profile.execution.sandbox == "worktree"
    assert profile.execution.timeout_minutes == 60
    assert profile.execution.max_parallel_agents == 3


def test_completion_contract_scope_check() -> None:
    contract = CompletionContract(
        task_id="task-1",
        required=[Check(type="pytest", command="pytest tests/ -v")],
        optional=[Check(type="mypy", command="mypy src/", expect_exit=0)],
        forbidden_path_check=ForbiddenPathCheck(paths=["/etc/passwd"]),
        scope_check=ScopeCheck(
            description="Only touch controller schemas",
            allowed_paths=["src/governance_controller/governance_controller/schemas/"],
            forbidden_paths=["src/governance_controller/tests/"],
        ),
    )

    assert contract.task_id == "task-1"
    assert len(contract.required) == 1
    assert contract.required[0].type == "pytest"
    assert contract.required[0].command == "pytest tests/ -v"
    assert contract.required[0].expect_exit == 0
    assert len(contract.optional) == 1
    assert contract.forbidden_path_check.paths == ["/etc/passwd"]
    assert contract.scope_check.description == "Only touch controller schemas"
    assert contract.scope_check.allowed_paths == [
        "src/governance_controller/governance_controller/schemas/"
    ]
    assert contract.scope_check.forbidden_paths == ["src/governance_controller/tests/"]


def test_task_contract_execution_defaults() -> None:
    contract = TaskContract(
        task_id="task-2",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Test defaults",
        acceptance=["Default execution config is used"],
    )

    assert contract.execution.team == "default"
    assert contract.execution.harness == "opencode"
    assert contract.execution.timeout_minutes == 60
    assert contract.execution.max_retries == 2
