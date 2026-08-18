from __future__ import annotations

import re


class GitCascadeService:
    _VALID_BRANCH = re.compile(r"^[a-zA-Z0-9_.-]+$")
    _MAX_BRANCH_LENGTH = 100

    @classmethod
    def land(
        cls, task_id: str, branch: str, target: str = "main"
    ) -> dict[str, object]:
        return {
            "task_id": task_id,
            "branch": branch,
            "target": target,
            "status": "landed",
            "merge_commit": f"cascade-{task_id}",
        }

    @classmethod
    def validate_branch_name(cls, branch: str) -> bool:
        if not isinstance(branch, str):
            return False
        if len(branch) > cls._MAX_BRANCH_LENGTH:
            return False
        return bool(cls._VALID_BRANCH.match(branch))
