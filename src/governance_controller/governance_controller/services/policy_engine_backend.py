"""Pluggable policy backend selector.

The Controller keeps policy enforcement logic in PolicyEngine but can delegate
 the actual evaluation to an external Open Policy Agent (OPA) when configured.
 When OPA is not configured, the embedded evaluator is used.
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
        policy_engine: type[PolicyEngine] | None = None,
    ) -> PolicyResult:
        """Return a PolicyResult using OPA if configured, else embedded engine."""
        if settings.opa_base_url:
            return await self._evaluate_opa(contract, profile, approval_type)
        engine = policy_engine or PolicyEngine
        return engine.evaluate(contract, profile, approval_type)

    async def _evaluate_opa(
        self,
        contract: TaskContract,
        profile: ProjectProfile,
        approval_type: ApprovalType,
    ) -> PolicyResult:
        client = self._opa
        if client is None:
            client = OPAClient()

        input_data: dict[str, object] = {
            "contract": contract.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "approval_type": approval_type.value,
        }

        try:
            result = await client.evaluate(input_data)
        except OPAClientError as exc:
            # Fail closed: OPA unreachable -> deny.
            return PolicyResult(
                allowed=False,
                violations=[f"OPA policy evaluation failed: {exc}"],
            )

        allowed = bool(result.get("allow"))
        violations = result.get("violations")
        if not isinstance(violations, list):
            violations = []
        violations = [str(v) for v in violations]

        return PolicyResult(allowed=allowed, violations=violations)
