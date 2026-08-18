"""Global harness provider registry."""

from governance_controller.harness.base import HarnessProvider
from governance_controller.harness.claude_code import CLAUDE_CODE
from governance_controller.harness.opencode import OPEN_CODE


class _HarnessProviderRegistry:
    """In-memory registry of supported harness providers.

    The registry is intentionally simple: it stores harness metadata so that
    policy and execution components can reason about which harnesses are
    available without spawning external processes or holding credentials.
    """

    def __init__(self) -> None:
        self._providers: dict[str, HarnessProvider] = {}
        self.register(OPEN_CODE)
        self.register(CLAUDE_CODE)

    def register(self, provider: HarnessProvider) -> None:
        """Register a harness provider by name."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> HarnessProvider:
        """Return the provider with the given name.

        Raises:
            KeyError: if no provider with ``name`` is registered.
        """
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Harness provider '{name}' is not registered") from exc

    def list(self) -> list[str]:
        """Return the names of all registered providers."""
        return list(self._providers.keys())

    def is_registered(self, name: str) -> bool:
        """Return whether a provider with the given name is registered."""
        return name in self._providers


registry = _HarnessProviderRegistry()
