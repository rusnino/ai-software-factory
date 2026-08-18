from governance_controller.schemas.task_contract import TaskContract


class VerificationService:
    """Deterministic synthetic verification harness.

    If the task contract includes a :class:`CompletionContract`, the service
    reports results driven by that contract. Otherwise it falls back to a
    minimal static check of the contract's own ``forbidden_paths`` field.
    """

    @staticmethod
    def verify(contract: TaskContract) -> dict:
        checks: list[dict] = []
        passed = True

        completion = contract.completion_contract
        if completion is not None:
            # Required checks are reported as passed in this stub; real
            # implementation would invoke the commands and compare exit codes.
            for check in completion.required:
                checks.append(
                    {
                        "name": f"required:{check.type}",
                        "status": "passed",
                        "command": check.command,
                    }
                )
            for check in completion.optional:
                checks.append(
                    {
                        "name": f"optional:{check.type}",
                        "status": "passed",
                        "command": check.command,
                    }
                )

            # Forbidden path check driven by the completion contract.
            touched_paths = set(contract.inputs + contract.deliverables)
            forbidden_touches = touched_paths & set(
                completion.forbidden_path_check.paths
            )
            if forbidden_touches:
                passed = False
                checks.append(
                    {
                        "name": "forbidden_paths",
                        "status": "failed",
                        "detail": sorted(forbidden_touches),
                    }
                )
            else:
                checks.append({"name": "forbidden_paths", "status": "passed"})

            # Scope check stub: treat forbidden/allowed paths as prefixes.
            scope_forbidden = completion.scope_check.forbidden_paths
            scope_conflicts = {
                p
                for p in touched_paths
                if any(p.startswith(prefix) for prefix in scope_forbidden)
            }
            if scope_conflicts:
                passed = False
                checks.append(
                    {
                        "name": "scope",
                        "status": "failed",
                        "detail": sorted(scope_conflicts),
                    }
                )
            else:
                checks.append({"name": "scope", "status": "passed"})
        else:
            has_forbidden_path = contract.forbidden_paths and (
                "__pycache__" in contract.forbidden_paths
                or ".env" in contract.forbidden_paths
            )

            forbidden_status = "failed" if has_forbidden_path else "passed"
            passed = forbidden_status == "passed"
            checks = [
                {"name": "schema", "status": "passed"},
                {"name": "forbidden_paths", "status": forbidden_status},
                {"name": "syntax", "status": "passed"},
            ]

        return {
            "contract_id": contract.task_id,
            "passed": passed,
            "checks": checks,
        }
