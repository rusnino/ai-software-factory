from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.verification_service import VerificationService


def test_default_contract_passes_all_checks() -> None:
    contract = TaskContract(
        task_id="task-001",
        project_id="project-001",
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
