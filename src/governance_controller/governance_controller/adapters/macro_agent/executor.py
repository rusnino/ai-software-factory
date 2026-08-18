"""Governance Controller executor wrapper around macro-agent client."""

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.schemas.task_contract import TaskContract


class MacroAgentExecutor:
    """Start, query, and collect results from a macro-agent run."""

    def __init__(self, client: MacroAgentClient | None = None) -> None:
        self.client = client or MacroAgentClient()

    async def start(self, task_contract: TaskContract) -> dict:
        """Start a macro-agent run for the given task contract."""
        payload = {
            "task_id": task_contract.task_id,
            "team": task_contract.execution.team,
            "harness": task_contract.execution.harness,
            "objective": task_contract.objective,
            "acceptance": task_contract.acceptance,
            "metadata": {
                "controller_task_id": task_contract.task_id,
                "controller_execution_id": None,
                "opentasks_id": None,
                "project_id": task_contract.project_id,
            },
        }
        return await self.client.start(payload)

    async def status(self, run_id: str) -> dict:
        """Query macro-agent run status."""
        return await self.client.status(run_id)

    async def cancel(self, run_id: str) -> dict:
        """Cancel a macro-agent run."""
        return await self.client.cancel(run_id)

    async def collect(self, run_id: str) -> dict:
        """Collect macro-agent run results."""
        return await self.client.collect(run_id)
