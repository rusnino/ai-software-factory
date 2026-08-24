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
            "role": execution.role,
            "objective": task_contract.objective,
            "acceptance": task_contract.acceptance,
            "timeout_minutes": execution.timeout_minutes,
            "max_retries": execution.max_retries,
            "sandbox": sandbox,
            "max_parallel_agents": max_parallel_agents,
            "uses_docker_socket": execution.uses_docker_socket,
            "destructive_shell": execution.destructive_shell,
            "spawn_subagents": execution.spawn_subagents,
            "network_access": execution.network_access,
            "force_push": execution.force_push,
            "signed_commits": execution.signed_commits,
            "metadata": {
                "controller_task_id": task_contract.task_id,
                "controller_execution_id": controller_execution_id,
                "opentasks_id": None,
                "project_id": task_contract.project_id,
            },
        }
        if task_contract.opentasks_dag is not None:
            payload["opentasks_dag"] = task_contract.opentasks_dag
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
