from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/governance"

    class Config:
        env_prefix = "GC_"


settings = Settings()
