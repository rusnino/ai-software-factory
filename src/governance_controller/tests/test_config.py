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
