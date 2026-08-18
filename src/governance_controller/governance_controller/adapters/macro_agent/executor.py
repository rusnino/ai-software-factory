"""Governance Controller executor wrapper around macro-agent client."""

from typing import Any

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.schemas.task_contract import TaskContract


class MacroAgentExecutor:
    """Start, query, and collect results from a macro-agent run."""

    def __init__(self, client: MacroAgentClient | None = None) -> None:
        self.client = client or MacroAgentClient()

    async def start(
        self,
        task_contract: TaskContract,
        controller_execution_id: str,
        sandbox: str = "worktree",
        max_parallel_agents: int = 3,
    ) -> dict[str, Any]:
        """Start a macro-agent run for the given task contract."""
        execution = task_contract.execution
        payload: dict[str, object] = {
            "task_id": task_contract.task_id,
            "team": execution.team,
            "harness": execution.harness,
            "objective": task_contract.objective,
            "acceptance": task_contract.acceptance,
            "timeout_minutes": execution.timeout_minutes,
            "max_retries": execution.max_retries,
            "sandbox": sandbox,
            "max_parallel_agents": max_parallel_agents,
            "metadata": {
                "controller_task_id": task_contract.task_id,
                "controller_execution_id": controller_execution_id,
                "opentasks_id": None,
                "project_id": task_contract.project_id,
            },
        }
        return await self.client.start(payload)

    async def status(self, run_id: str) -> dict[str, Any]:
        """Query macro-agent run status."""
        return await self.client.status(run_id)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancel a macro-agent run."""
        return await self.client.cancel(run_id)

    async def collect(self, run_id: str) -> dict[str, Any]:
        """Collect macro-agent run results."""
        return await self.client.collect(run_id)
