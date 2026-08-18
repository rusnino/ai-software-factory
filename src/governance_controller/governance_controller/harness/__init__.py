"""Harness provider registry package."""

from governance_controller.harness.base import HarnessProvider
from governance_controller.harness.registry import registry

__all__ = ["HarnessProvider", "registry"]
