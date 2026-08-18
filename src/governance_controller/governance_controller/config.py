from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables are expected to be prefixed with ``GC_``.
    """

    model_config = SettingsConfigDict(env_prefix="GC_")

    # Dev-only default; production must inject a real DATABASE_URL.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/governance"
    macro_agent_base_url: str = "http://localhost:3000"
    macro_agent_timeout_seconds: float = 30.0

    # Telegram webhook authentication. The secret token is sent by Telegram in
    # the ``X-Telegram-Bot-Api-Secret-Token`` header when webhooks are
    # configured with a secret_token. Set to a non-empty value and route the
    # header to ``TelegramAdapter`` to validate that updates come from Telegram.
    telegram_webhook_secret_token: str = ""


settings = Settings()
