from governance_controller.schemas import (
    Check,
    CompletionContract,
    ForbiddenPathCheck,
    ScopeCheck,
)
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.verification_service import VerificationService


async def test_default_contract_passes_all_checks() -> None:
    contract = TaskContract(
        task_id="task-001",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something",
        acceptance=["It works"],
    )

    result = await VerificationService.verify_execution(contract)

    assert result == {
        "contract_id": "task-001",
        "passed": True,
        "checks": [
            {"name": "schema", "status": "passed"},
            {"name": "forbidden_paths", "status": "passed"},
            {"name": "syntax", "status": "passed"},
        ],
    }


async def test_forbidden_path_fails_verification() -> None:
    contract = TaskContract(
        task_id="task-002",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something bad",
        acceptance=["It works"],
        forbidden_paths=[".env", "README.md"],
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-002"
    assert result["passed"] is False
    assert result["checks"] == [
        {"name": "schema", "status": "passed"},
        {"name": "forbidden_paths", "status": "failed"},
        {"name": "syntax", "status": "passed"},
    ]


async def test_completion_contract_executes_required_checks() -> None:
    contract = TaskContract(
        task_id="task-003",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something with completion contract",
        acceptance=["It works"],
        inputs=["/etc/passwd"],
        completion_contract=CompletionContract(
            task_id="task-003",
            required=[
                Check(type="true", command="true"),
                Check(type="false", command="false", expect_exit=1),
            ],
            forbidden_path_check=ForbiddenPathCheck(paths=["/etc/passwd"]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["contract_id"] == "task-003"
    assert result["passed"] is False
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["required:true"]["status"] == "passed"
    assert checks["required:false"]["status"] == "passed"
    assert checks["forbidden_paths"]["status"] == "failed"
    assert checks["scope"]["status"] == "passed"


async def test_completion_contract_required_failure_fails_verification() -> None:
    contract = TaskContract(
        task_id="task-003b",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something with a failing required check",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-003b",
            required=[Check(type="false", command="false", expect_exit=0)],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    check = next(c for c in result["checks"] if c["name"] == "required:false")
    assert check["status"] == "failed"
    assert check["actual_exit"] == 1
    assert check["expected_exit"] == 0


async def test_forbidden_path_prefix_match_fails() -> None:
    contract = TaskContract(
        task_id="task-020",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Touch a subpath of a forbidden directory",
        acceptance=["It is caught"],
        inputs=["~/.ssh/id_rsa"],
        completion_contract=CompletionContract(
            task_id="task-020",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=["~/.ssh"]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    forbidden = next(c for c in result["checks"] if c["name"] == "forbidden_paths")
    assert forbidden["status"] == "failed"
    assert "~/.ssh/id_rsa" in forbidden["detail"]


async def test_completion_contract_scope_conflict_fails() -> None:
    contract = TaskContract(
        task_id="task-004",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something out of scope",
        acceptance=["It works"],
        deliverables=["tests/test_x.py"],
        completion_contract=CompletionContract(
            task_id="task-004",
            required=[],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is False
    scope_check = next(c for c in result["checks"] if c["name"] == "scope")
    assert scope_check["status"] == "failed"
    assert "tests/test_x.py" in scope_check["detail"]


async def test_optional_check_failure_does_not_fail_verification() -> None:
    contract = TaskContract(
        task_id="task-005",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something optional",
        acceptance=["It works"],
        completion_contract=CompletionContract(
            task_id="task-005",
            required=[],
            optional=[Check(type="false", command="false", expect_exit=0)],
            forbidden_path_check=ForbiddenPathCheck(paths=[]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=[],
                forbidden_paths=[],
            ),
        ),
    )

    result = await VerificationService.verify_execution(contract)

    assert result["passed"] is True
    optional = next(c for c in result["checks"] if c["name"] == "optional:false")
    assert optional["status"] == "failed"
