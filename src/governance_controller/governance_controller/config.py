from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables are expected to be prefixed with ``GC_``.
    """

    model_config = SettingsConfigDict(env_prefix="GC_")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/governance"
    macro_agent_base_url: str = "http://localhost:3000"


settings = Settings()
