from datetime import datetime

import pytest

from governance_controller.constants import ApprovalType, TaskState
from governance_controller.models.task import Task
from governance_controller.services.state_machine import StateMachine


class TestTaskModel:
    def test_task_defaults_to_proposed_state(self) -> None:
        task = Task(id="task-1", project_id="proj-1")
        assert task.state == TaskState.PROPOSED
        assert task.task_contract_json == {}
        assert task.created_at is not None
        assert task.updated_at is None

    def test_task_fields(self) -> None:
        contract = {"scope": "implement feature X"}
        task = Task(
            id="task-2",
            state=TaskState.READY,
            project_id="proj-2",
            task_contract_json=contract,
            updated_at=datetime(2026, 8, 18, 12, 0, 0),
        )
        assert task.id == "task-2"
        assert task.state == TaskState.READY
        assert task.project_id == "proj-2"
        assert task.task_contract_json == contract
        assert task.updated_at == datetime(2026, 8, 18, 12, 0, 0)


class TestStateMachineValidTransitions:
    def test_proposed_to_plan_approved(self) -> None:
        task = Task(id="task-1", project_id="proj-1")
        before = task.updated_at
        result = StateMachine.transition(task, TaskState.PLAN_APPROVED)
        assert result.state == TaskState.PLAN_APPROVED
        assert result.updated_at is not None
        assert result.updated_at != before

    def test_human_review_to_done(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.HUMAN_REVIEW)
        result = StateMachine.transition(task, TaskState.DONE)
        assert result.state == TaskState.DONE

    def test_exec_approved_to_ready(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.EXEC_APPROVED)
        result = StateMachine.transition(task, TaskState.READY)
        assert result.state == TaskState.READY

    def test_ready_to_running(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.READY)
        result = StateMachine.transition(task, TaskState.RUNNING)
        assert result.state == TaskState.RUNNING

    def test_running_to_agent_review(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.RUNNING)
        result = StateMachine.transition(task, TaskState.AGENT_REVIEW)
        assert result.state == TaskState.AGENT_REVIEW

    def test_running_to_blocked(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.RUNNING)
        result = StateMachine.transition(task, TaskState.BLOCKED)
        assert result.state == TaskState.BLOCKED

    def test_agent_review_to_human_review(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.AGENT_REVIEW)
        result = StateMachine.transition(task, TaskState.HUMAN_REVIEW)
        assert result.state == TaskState.HUMAN_REVIEW

    def test_blocked_to_running(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.BLOCKED)
        result = StateMachine.transition(task, TaskState.RUNNING)
        assert result.state == TaskState.RUNNING

    def test_human_review_to_running_is_forbidden(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.HUMAN_REVIEW)
        with pytest.raises(ValueError, match="HUMAN_REVIEW -> RUNNING"):
            StateMachine.transition(task, TaskState.RUNNING)


class TestStateMachineInvalidTransitions:
    def test_proposed_to_running_requires_plan_approved(self) -> None:
        task = Task(id="task-1", project_id="proj-1")
        with pytest.raises(ValueError, match="PROPOSED -> RUNNING"):
            StateMachine.transition(task, TaskState.RUNNING)

    def test_running_to_done_requires_human_review(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.RUNNING)
        with pytest.raises(ValueError, match="RUNNING -> DONE"):
            StateMachine.transition(task, TaskState.DONE)

    def test_proposed_to_exec_approved_requires_plan_approval(self) -> None:
        task = Task(id="task-1", project_id="proj-1")
        with pytest.raises(ValueError, match="PROPOSED -> EXEC_APPROVED"):
            StateMachine.transition(task, TaskState.EXEC_APPROVED)

    def test_plan_approved_to_ready_requires_exec_approval(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.PLAN_APPROVED)
        with pytest.raises(ValueError, match="PLAN_APPROVED -> READY"):
            StateMachine.transition(task, TaskState.READY)

    def test_exec_approved_to_running_requires_ready(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.EXEC_APPROVED)
        with pytest.raises(ValueError, match="EXEC_APPROVED -> RUNNING"):
            StateMachine.transition(task, TaskState.RUNNING)

    def test_done_is_terminal(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.DONE)
        with pytest.raises(ValueError, match="DONE -> RUNNING"):
            StateMachine.transition(task, TaskState.RUNNING)

    def test_failed_is_terminal(self) -> None:
        task = Task(id="task-1", project_id="proj-1", state=TaskState.FAILED)
        with pytest.raises(ValueError, match="FAILED -> PROPOSED"):
            StateMachine.transition(task, TaskState.PROPOSED)


class TestApprovalTypes:
    def test_approval_type_values(self) -> None:
        assert ApprovalType.PLAN == "plan"
        assert ApprovalType.EXECUTION == "execution"
        assert ApprovalType.MERGE == "merge"
