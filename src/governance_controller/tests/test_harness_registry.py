"""Tests for the harness provider registry."""

import pytest

from governance_controller.harness import registry
from governance_controller.harness.base import HarnessProvider


def test_registry_contains_opencode_and_claude_code() -> None:
    names = registry.list()
    assert "opencode" in names
    assert "claude-code" in names


def test_get_opencode_returns_expected_command() -> None:
    provider = registry.get("opencode")
    assert provider.command == "opencode"
    assert provider.auth == "provider-configured"
    assert provider.supports_mcp is True
    assert provider.allowed_roles == ["worker", "reviewer", "planner", "meta"]


def test_claude_code_allowed_roles_match_spec_06() -> None:
    provider = registry.get("claude-code")
    assert provider.allowed_roles == ["planner", "architect", "reviewer"]


def test_get_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        registry.get("unknown")


def test_is_registered_returns_correct_boolean() -> None:
    assert registry.is_registered("opencode") is True
    assert registry.is_registered("claude-code") is True
    assert registry.is_registered("codex") is False


def test_register_adds_new_provider() -> None:
    custom = HarnessProvider(
        name="aider",
        command="aider",
        auth="provider-configured",
        supports_mcp=False,
        allowed_roles=["worker"],
    )
    registry.register(custom)

    assert registry.is_registered("aider") is True
    assert registry.get("aider").command == "aider"
