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
            opa = await self._evaluate_opa(contract, profile, approval_type)
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
    ) -> PolicyResult:
        client = self._opa
        if client is None:
            client = OPAClient()

        input_data: dict[str, object] = {
            "contract": contract.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "approval_type": approval_type.value,
            "approval": {
                "actor": getattr(contract, "proposed_by", "unknown"),
                "type": approval_type.value,
            },
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
