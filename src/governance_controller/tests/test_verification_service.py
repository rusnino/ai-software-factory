from governance_controller.schemas import (
    Check,
    CompletionContract,
    ForbiddenPathCheck,
    ScopeCheck,
)
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.verification_service import VerificationService


def test_default_contract_passes_all_checks() -> None:
    contract = TaskContract(
        task_id="task-001",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something",
        acceptance=["It works"],
    )

    result = VerificationService.verify(contract)

    assert result == {
        "contract_id": "task-001",
        "passed": True,
        "checks": [
            {"name": "schema", "status": "passed"},
            {"name": "forbidden_paths", "status": "passed"},
            {"name": "syntax", "status": "passed"},
        ],
    }


def test_forbidden_path_fails_verification() -> None:
    contract = TaskContract(
        task_id="task-002",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something bad",
        acceptance=["It works"],
        forbidden_paths=[".env", "README.md"],
    )

    result = VerificationService.verify(contract)

    assert result["contract_id"] == "task-002"
    assert result["passed"] is False
    assert result["checks"] == [
        {"name": "schema", "status": "passed"},
        {"name": "forbidden_paths", "status": "failed"},
        {"name": "syntax", "status": "passed"},
    ]


def test_completion_contract_drives_verification() -> None:
    contract = TaskContract(
        task_id="task-003",
        project_id="project-001",
        proposed_by="agent-1",
        objective="Do something with completion contract",
        acceptance=["It works"],
        inputs=["/etc/passwd"],
        completion_contract=CompletionContract(
            task_id="task-003",
            required=[Check(type="pytest", command="pytest tests/ -v")],
            forbidden_path_check=ForbiddenPathCheck(paths=["/etc/passwd"]),
            scope_check=ScopeCheck(
                description="Only touch controller code",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
            ),
        ),
    )

    result = VerificationService.verify(contract)

    assert result["contract_id"] == "task-003"
    assert result["passed"] is False
    check_names = {c["name"] for c in result["checks"]}
    assert "required:pytest" in check_names
    assert "forbidden_paths" in check_names
    assert "scope" in check_names


def test_completion_contract_scope_conflict_fails() -> None:
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

    result = VerificationService.verify(contract)

    assert result["passed"] is False
    scope_check = next(c for c in result["checks"] if c["name"] == "scope")
    assert scope_check["status"] == "failed"
    assert "tests/test_x.py" in scope_check["detail"]
