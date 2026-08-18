"""Harness provider metadata."""

from dataclasses import dataclass, field


@dataclass
class HarnessProvider:
    """Metadata for an agent harness executable.

    This model intentionally contains only auth *type* information, never
    credentials. Credentials are resolved at runtime from provider/user
    configuration or subscription secrets configured out of band.
    """

    name: str
    command: str
    auth: str  # "subscription" or "provider-configured"
    supports_mcp: bool = True
    allowed_roles: list[str] = field(
        default_factory=lambda: ["worker", "reviewer", "planner"]
    )
