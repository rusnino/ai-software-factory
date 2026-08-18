"""Application configuration and settings."""

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog._log_levels import NAME_TO_LEVEL


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables are expected to be prefixed with ``GC_``.
    """

    model_config = SettingsConfigDict(env_prefix="GC_")

    # Dev-only default; production must inject a real DATABASE_URL.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/governance"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 30
    database_pool_pre_ping: bool = True
    macro_agent_base_url: str = "http://localhost:3000"
    macro_agent_timeout_seconds: float = 30.0
    log_level: str = "info"

    # Telegram webhook authentication. The secret token is sent by Telegram in
    # the ``X-Telegram-Bot-Api-Secret-Token`` header when webhooks are
    # configured with a secret_token. Set to a non-empty value and route the
    # header to ``TelegramAdapter`` to validate that updates come from Telegram.
    telegram_webhook_secret_token: str = ""


def configure_logging(log_level: str) -> None:
    """Configure structlog using the provided log level."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            NAME_TO_LEVEL.get(log_level.lower(), 20)
        ),
    )


settings = Settings()
configure_logging(settings.log_level)
