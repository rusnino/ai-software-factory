"""Pluggable policy backend selector.

The Controller always runs the embedded PolicyEngine first. When an external
Open Policy Agent (OPA) is configured, it is consulted as an additional
enforcement layer on top of the embedded checks, never as a replacement.
This guarantees that the hardened Phase 1 rules (wrapper/interpreter rejection,
forbidden-path checks, destructive-flag checks, git-config checks,
container-escape checks, etc.) cannot be silently bypassed by enabling OPA.
"""


from governance_controller.adapters.opa_client import OPAClient, OPAClientError
from governance_controller.config import settings
from governance_controller.constants import ApprovalType
from governance_controller.schemas.project_profile import ProjectProfile
from governance_controller.schemas.task_contract import TaskContract
from governance_controller.services.policy_engine import (
    PolicyEngine,
    PolicyResult,
)


class PolicyEngineBackend:
    """Evaluate policy via embedded PolicyEngine or optional OPA backend."""

    def __init__(self, opa_client: OPAClient | None = None) -> None:
        self._opa = opa_client

    async def evaluate(
        self,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
        actor: str | None = None,
        policy_engine: type[PolicyEngine] | None = None,
    ) -> PolicyResult:
        """Return a PolicyResult.

        Always runs the embedded PolicyEngine first. If OPA is configured, the
        embedded result must be allowed before OPA is consulted; OPA can add
        extra violations but cannot override an embedded denial.
        """
        engine = policy_engine or PolicyEngine
        embedded = engine.evaluate(contract, profile, approval_type)
        if not embedded.allowed:
            return embedded

        if settings.opa_base_url:
            opa = await self._evaluate_opa(
                contract, profile, approval_type, actor=actor
            )
            if not opa.allowed:
                return opa
            # Both allowed: merge violations (should be empty) and return.
            return PolicyResult(
                allowed=True,
                violations=list({*embedded.violations, *opa.violations}),
            )

        return embedded

    async def _evaluate_opa(
        self,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
        actor: str | None = None,
    ) -> PolicyResult:
        client = self._opa
        if client is None:
            client = OPAClient()

        input_data = self._minimal_opa_input(
            contract, profile, approval_type, actor=actor
        )

        try:
            result = await client.evaluate(input_data)
        except OPAClientError as exc:
            # Fail closed: OPA unreachable -> deny.
            return PolicyResult(
                allowed=False,
                violations=[f"OPA policy evaluation failed: {exc}"],
            )

        allow_value = result.get("allow")
        allowed = allow_value is True
        violations = result.get("violations")
        if not isinstance(violations, list):
            violations = []
        violations = [str(v) for v in violations]
        return PolicyResult(allowed=allowed, violations=violations)

    @staticmethod
    def _minimal_opa_input(
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
        actor: str | None = None,
    ) -> dict[str, object]:
        """Return a data-minimized input document for OPA.

        Only the fields required for policy decisions are sent to the OPA
        server. Task descriptions, full project metadata, and other non-policy
        fields are deliberately omitted to limit exposure if the OPA endpoint
        is compromised or misconfigured.
        """
        commands: list[str] = []
        verification = contract.verification or {}
        raw_commands = verification.get("commands", [])
        if isinstance(raw_commands, list):
            commands.extend(
                str(cmd) for cmd in raw_commands if isinstance(cmd, str)
            )
        completion = contract.completion_contract
        if completion is not None:
            for check in completion.required:
                commands.append(check.command)
            for check in completion.optional:
                commands.append(check.command)

        allowed_harnesses: list[str] = []
        if profile.execution and profile.execution.allowed_harnesses:
            allowed_harnesses = list(profile.execution.allowed_harnesses)

        execution = contract.execution
        security = profile.security
        git = profile.git

        return {
            "task_id": contract.task_id,
            "project_id": contract.project_id,
            "proposed_by": contract.proposed_by,
            "approval_type": approval_type.value,
            "approval": {
                "actor": actor or "",
                "type": approval_type.value,
            },
            "execution": {
                "harness": execution.harness,
                "role": execution.role,
                "uses_docker_socket": execution.uses_docker_socket,
                "destructive_shell": execution.destructive_shell,
                "network_access": execution.network_access,
                "spawn_subagents": execution.spawn_subagents,
                "force_push": execution.force_push,
                "signed_commits": execution.signed_commits,
            },
            "commands": commands,
            "forbidden_paths": list(contract.forbidden_paths),
            "allowed_harnesses": allowed_harnesses,
            "security": {
                "docker_socket": security.docker_socket,
                "destructive_shell": security.destructive_shell,
                "network": security.network,
                "spawn_subagents": security.spawn_subagents,
                "forbidden_paths": list(security.forbidden_paths),
            },
            "git": {
                "merge_requires_human": git.merge_requires_human,
                "force_push": git.force_push,
                "signed_commits": git.signed_commits,
            },
        }

