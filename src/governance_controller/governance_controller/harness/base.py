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
    # Per SPEC-06 §6.2. Each concrete harness must override this; there is no
    # safe universal default because different harnesses support different roles.
    allowed_roles: list[str] = field(default_factory=list)
