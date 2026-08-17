from governance_controller.config import Settings


def test_default_database_url() -> None:
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://postgres:postgres@localhost/governance"
