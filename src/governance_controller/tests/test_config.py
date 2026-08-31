import os

import pytest

from governance_controller.config import Settings


def test_default_database_url() -> None:
    settings = Settings()
    assert (
        settings.database_url
        == "postgresql+asyncpg://postgres:postgres@localhost/governance"
    )


def test_default_macro_agent_timeout_seconds() -> None:
    settings = Settings()
    assert settings.macro_agent_timeout_seconds == 30.0


def test_plane_property_ids_default_to_empty() -> None:
    settings = Settings()

    assert settings.plane_controller_task_id_property_id == ""
    assert settings.plane_opentasks_id_property_id == ""
    assert settings.plane_source_property_id == ""
    assert settings.plane_approval_required_property_id == ""
    assert settings.plane_source_option_ids == {}


def test_plane_property_ids_read_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GC_PLANE_CONTROLLER_TASK_ID_PROPERTY_ID": "prop-controller-task",
        "GC_PLANE_OPENTASKS_ID_PROPERTY_ID": "prop-opentasks",
        "GC_PLANE_SOURCE_PROPERTY_ID": "prop-source",
        "GC_PLANE_APPROVAL_REQUIRED_PROPERTY_ID": "prop-approval",
        "GC_PLANE_SOURCE_OPTION_IDS": '{"telegram":"option-telegram"}',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.plane_controller_task_id_property_id == "prop-controller-task"
    assert settings.plane_opentasks_id_property_id == "prop-opentasks"
    assert settings.plane_source_property_id == "prop-source"
    assert settings.plane_approval_required_property_id == "prop-approval"
    assert settings.plane_source_option_ids == {"telegram": "option-telegram"}


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


def test_zero_pool_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool size and max_overflow must be positive"):
        Settings(database_pool_size=0)


def test_zero_max_overflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool size and max_overflow must be positive"):
        Settings(database_max_overflow=0)


def test_negative_pool_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool size and max_overflow must be positive"):
        Settings(database_pool_size=-5)


def test_negative_max_overflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool size and max_overflow must be positive"):
        Settings(database_max_overflow=-1)


def test_negative_pool_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="pool timeout must be non-negative"):
        Settings(database_pool_timeout=-30)


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        Settings(log_level="bogus")
