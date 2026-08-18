# Task 17 Brief: Verification Stub

**Goal:** Add a verification service that accepts a task contract and returns deterministic synthetic verification results. This simulates the verification harness before real CI/test integrations exist.

**Files to create:**
- `src/governance_controller/governance_controller/services/verification_service.py`
- `src/governance_controller/tests/test_verification_service.py`

**Exact values to use verbatim:**

`VerificationService.verify(contract: TaskContract) -> dict`:
```python
return {
    "contract_id": contract.task_id,
    "passed": True,
    "checks": [
        {"name": "schema", "status": "passed"},
        {"name": "forbidden_paths", "status": "passed"},
        {"name": "syntax", "status": "passed"},
    ],
}
```

If `contract.forbidden_paths` is non-empty and contains `"__pycache__"` or `".env"`, mark `forbidden_paths` check as `failed` and `passed` as `False`.

**Interfaces produced:**
- `VerificationService` from `governance_controller.services.verification_service`

**Interfaces consumed:**
- `TaskContract` from `governance_controller.schemas.task_contract`

**Constraints:**
- No external CI calls in Phase 1.
- Deterministic output for tests.

**Verification steps:**
1. Write `tests/test_verification_service.py` covering:
   - default contract passes all checks.
   - forbidden path triggers failure.
2. Run `uv run pytest tests/test_verification_service.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add deterministic verification service stub`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-17-report.md`
