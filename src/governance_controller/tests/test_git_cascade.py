import pytest

from governance_controller.services.git_cascade import GitCascadeService


class TestGitCascadeService:
    def test_land_returns_deterministic_merge_record(self) -> None:
        result = GitCascadeService.land("task-123", "feature/task-123")
        assert result == {
            "task_id": "task-123",
            "branch": "feature/task-123",
            "target": "main",
            "status": "landed",
            "merge_commit": "cascade-task-123",
        }

    @pytest.mark.parametrize(
        "branch",
        [
            "main",
            "feature-task-123",
            "bugfix_issue-42",
            "release-v1.0.0",
            "a" * 100,
        ],
    )
    def test_validate_branch_name_accepts_valid_names(self, branch: str) -> None:
        assert GitCascadeService.validate_branch_name(branch) is True

    @pytest.mark.parametrize(
        "branch",
        [
            "../main",
            "branch;rm",
            "feature/task 123",
            "feature/task|123",
            "feature/task&123",
            "feature/task$123",
            "feature/task`123",
            "feature/task(123)",
            "a" * 101,
            "",
            "/absolute/path",
            "./relative/path",
        ],
    )
    def test_validate_branch_name_rejects_invalid_names(self, branch: str) -> None:
        assert GitCascadeService.validate_branch_name(branch) is False
