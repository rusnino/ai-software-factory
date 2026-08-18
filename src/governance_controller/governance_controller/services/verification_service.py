from governance_controller.schemas.task_contract import TaskContract


class VerificationService:
    """Deterministic synthetic verification harness stub."""

    @staticmethod
    def verify(contract: TaskContract) -> dict:
        has_forbidden_path = contract.forbidden_paths and (
            "__pycache__" in contract.forbidden_paths
            or ".env" in contract.forbidden_paths
        )

        forbidden_status = "failed" if has_forbidden_path else "passed"

        return {
            "contract_id": contract.task_id,
            "passed": forbidden_status == "passed",
            "checks": [
                {"name": "schema", "status": "passed"},
                {"name": "forbidden_paths", "status": forbidden_status},
                {"name": "syntax", "status": "passed"},
            ],
        }
