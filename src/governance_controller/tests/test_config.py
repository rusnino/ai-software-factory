import os

import pytest

from governance_controller.config import Settings


def test_default_database_url() -> None:
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://postgres:postgres@localhost/governance"


def test_default_macro_agent_timeout_seconds() -> None:
    settings = Settings()
    assert settings.macro_agent_timeout_seconds == 30.0


def test_macro_agent_timeout_seconds_can_be_overridden() -> None:
    settings = Settings(macro_agent_timeout_seconds=7.5)
    assert settings.macro_agent_timeout_seconds == 7.5


def test_default_log_level() -> None:
    settings = Settings()
    assert settings.log_level == "info"


@pytest.mark.parametrize(
    "env_value,expected",
    [("debug", "debug"), ("warning", "warning")],
)
def test_log_level_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected: str,
) -> None:
    monkeypatch.setenv("GC_LOG_LEVEL", env_value)
    settings = Settings()
    assert settings.log_level == expected
    assert os.environ.get("GC_LOG_LEVEL") == env_value


def test_negative_pool_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool settings must be non-negative"):
        Settings(database_pool_size=-5)


def test_negative_max_overflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool settings must be non-negative"):
        Settings(database_max_overflow=-1)


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        Settings(log_level="bogus")
