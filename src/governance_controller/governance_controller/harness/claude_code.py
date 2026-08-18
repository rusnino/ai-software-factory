"""Claude Code harness provider metadata."""

from governance_controller.harness.base import HarnessProvider

# Claude Code requires an Anthropic subscription. No credential is stored here.
CLAUDE_CODE = HarnessProvider(
    name="claude-code",
    command="claude",
    auth="subscription",
    supports_mcp=True,
    allowed_roles=["planner", "architect", "reviewer"],
)
