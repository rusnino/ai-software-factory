"""OpenCode harness provider metadata."""

from governance_controller.harness.base import HarnessProvider

# OpenCode is the primary harness. It is authenticated by the provider/user
# configuration present in the environment, not by a subscription key.
OPEN_CODE = HarnessProvider(
    name="opencode",
    command="opencode",
    auth="provider-configured",
    supports_mcp=True,
)
