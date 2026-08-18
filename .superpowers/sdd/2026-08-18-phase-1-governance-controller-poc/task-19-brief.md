# Task 19 Brief: Git-Cascade Landing Stub

**Goal:** Provide a service that records the intent to land agent changes via git cascade. Phase 1 stub returns a deterministic merge record without touching a real git repository.

**Files to create:**
- `src/governance_controller/governance_controller/services/git_cascade.py`
- `src/governance_controller/tests/test_git_cascade.py`

**Exact values to use verbatim:**

`GitCascadeService.land(task_id: str, branch: str, target: str = "main") -> dict`:
```python
return {
    "task_id": task_id,
    "branch": branch,
    "target": target,
    "status": "landed",
    "merge_commit": f"cascade-{task_id}",
}
```

`GitCascadeService.validate_branch_name(branch: str) -> bool`:
- Returns `True` if branch matches regex `^[a-zA-Z0-9_.-]+$` and length <= 100.
- Returns `False` otherwise.

**Interfaces produced:**
- `GitCascadeService` from `governance_controller.services.git_cascade`

**Interfaces consumed:**
- None.

**Constraints:**
- No real git operations in Phase 1.
- Branch name validation must prevent shell metacharacters and path traversal.

**Verification steps:**
1. Write `tests/test_git_cascade.py` covering:
   - `land` returns deterministic merge record.
   - valid branch names pass.
   - invalid branch names (e.g., `../main`, `branch;rm`) fail.
2. Run `uv run pytest tests/test_git_cascade.py -v`.
3. Run `uv run ruff check governance_controller tests`.

**Commit message:** `feat: add git-cascade landing stub with branch validation`

**Report file:** `.superpowers/sdd/2026-08-18-phase-1-governance-controller-poc/task-19-report.md`
