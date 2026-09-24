import pytest
from pydantic import ValidationError

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


def _nested_dict(depth: int) -> dict:
    """Build a dict nested *depth* levels deep: {"k": {"k": {...: {}}}}."""
    node: dict = {}
    for _ in range(depth):
        node = {"k": node}
    return node


def _oversized_dict() -> dict:
    """A wide-but-shallow dict whose JSON serialization exceeds the 32KB bound."""
    return {str(i): "x" * 100 for i in range(1000)}


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


@pytest.mark.parametrize(
    "default_branch",
    [
        "--output=/tmp/pwn",
        "-x",
        "--",
        "main~1",
        "main^",
        "main:file",
        "main?",
        "main*",
        "main[",
        "main\\x",
        "..",
        "feature/../main",
        "feature@{upstream}",
        "@",
        "/main",
        "main/",
        "feature//main",
        "main.",
        "main.lock",
        ".hidden",
        "main branch",
        "main\tbranch",
    ],
)
def test_default_branch_rejects_unsafe_ref_names(default_branch: str) -> None:
    """NEXT-37 / GH #418: `default_branch` is interpolated as a bare argv
    token into `git diff` with no `--` separator. A value starting with `-`
    is parsed by git as a command-line flag instead of a revision, letting an
    ordinary task proposer inject arbitrary git options (and, as a direct
    side effect, write to an arbitrary file on the Controller host). This
    must be rejected at profile-validation time, before any git subprocess
    is ever invoked with it -- fails pre-fix (any string was accepted),
    passes post-fix.
    """
    with pytest.raises(ValidationError):
        RepositoryConfig(path="/repo", default_branch=default_branch)


@pytest.mark.parametrize(
    "default_branch",
    ["main", "master", "release/1.0", "feature-x", "dev_branch", "v1.2.3"],
)
def test_default_branch_accepts_normal_branch_names(default_branch: str) -> None:
    config = RepositoryConfig(path="/repo", default_branch=default_branch)
    assert config.default_branch == default_branch


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


def test_task_contract_deeply_nested_opentasks_dag_rejected() -> None:
    """#377: a deeply-nested dict must fail schema validation (422), not
    crash pydantic-core's own recursion guard later in ``model_dump``."""
    with pytest.raises(ValidationError, match="nesting depth"):
        TaskContract(
            task_id="task-3",
            project_id="project-1",
            proposed_by="agent-1",
            objective="Reject deep nesting",
            acceptance=["Rejected before model_dump"],
            opentasks_dag=_nested_dict(300),
        )


def test_task_contract_deeply_nested_verification_rejected() -> None:
    """#377: ``verification`` shares the same unbounded-dict shape."""
    with pytest.raises(ValidationError, match="nesting depth"):
        TaskContract(
            task_id="task-4",
            project_id="project-1",
            proposed_by="agent-1",
            objective="Reject deep nesting",
            acceptance=["Rejected before model_dump"],
            verification=_nested_dict(300),
        )


def test_task_contract_moderately_nested_opentasks_dag_accepted() -> None:
    """A reasonable, real-world DAG shape is unaffected by the new bound."""
    contract = TaskContract(
        task_id="task-5",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Accept shallow nesting",
        acceptance=["Still works"],
        opentasks_dag=_nested_dict(5),
    )
    assert contract.opentasks_dag == _nested_dict(5)


def test_project_profile_deeply_nested_llm_rejected() -> None:
    """#377: ``ProjectProfile.llm``/``audit`` share the same unbounded shape."""
    with pytest.raises(ValidationError, match="nesting depth"):
        ProjectProfile(
            project_id="project-1",
            project_name="Test Project",
            repository=RepositoryConfig(path="/repo"),
            llm=_nested_dict(300),
        )


def test_project_profile_deeply_nested_audit_rejected() -> None:
    with pytest.raises(ValidationError, match="nesting depth"):
        ProjectProfile(
            project_id="project-1",
            project_name="Test Project",
            repository=RepositoryConfig(path="/repo"),
            audit=_nested_dict(300),
        )


def test_task_contract_oversized_verification_rejected() -> None:
    """#377: a wide-but-shallow payload must be bounded by size too."""
    with pytest.raises(ValidationError, match="serialized size"):
        TaskContract(
            task_id="task-6",
            project_id="project-1",
            proposed_by="agent-1",
            objective="Reject oversized payload",
            acceptance=["Rejected before model_dump"],
            verification=_oversized_dict(),
        )


def test_task_contract_oversized_opentasks_dag_rejected() -> None:
    """#377: ``opentasks_dag`` shares the same unbounded-size shape."""
    with pytest.raises(ValidationError, match="serialized size"):
        TaskContract(
            task_id="task-7",
            project_id="project-1",
            proposed_by="agent-1",
            objective="Reject oversized payload",
            acceptance=["Rejected before model_dump"],
            opentasks_dag=_oversized_dict(),
        )


def test_project_profile_oversized_llm_rejected() -> None:
    """#377: ``ProjectProfile.llm`` shares the same unbounded-size shape."""
    with pytest.raises(ValidationError, match="serialized size"):
        ProjectProfile(
            project_id="project-1",
            project_name="Test Project",
            repository=RepositoryConfig(path="/repo"),
            llm=_oversized_dict(),
        )


def test_project_profile_oversized_audit_rejected() -> None:
    """#377: ``ProjectProfile.audit`` shares the same unbounded-size shape."""
    with pytest.raises(ValidationError, match="serialized size"):
        ProjectProfile(
            project_id="project-1",
            project_name="Test Project",
            repository=RepositoryConfig(path="/repo"),
            audit=_oversized_dict(),
        )


def test_task_contract_verification_depth_19_accepted() -> None:
    """Boundary check for MAX_NESTING_DEPTH == 20: one below the limit."""
    contract = TaskContract(
        task_id="task-8",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Boundary: 19 levels",
        acceptance=["Accepted"],
        verification=_nested_dict(19),
    )
    assert contract.verification == _nested_dict(19)


def test_task_contract_verification_depth_20_accepted() -> None:
    """Boundary check for MAX_NESTING_DEPTH == 20: exactly at the documented
    limit must be accepted, not rejected off by one (#380 review)."""
    contract = TaskContract(
        task_id="task-9",
        project_id="project-1",
        proposed_by="agent-1",
        objective="Boundary: 20 levels",
        acceptance=["Accepted"],
        verification=_nested_dict(20),
    )
    assert contract.verification == _nested_dict(20)


def test_task_contract_verification_depth_21_rejected() -> None:
    """Boundary check for MAX_NESTING_DEPTH == 20: one past the limit."""
    with pytest.raises(ValidationError, match="nesting depth"):
        TaskContract(
            task_id="task-10",
            project_id="project-1",
            proposed_by="agent-1",
            objective="Boundary: 21 levels",
            acceptance=["Rejected"],
            verification=_nested_dict(21),
        )
