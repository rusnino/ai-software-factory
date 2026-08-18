from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/governance"
    macro_agent_base_url: str = "http://localhost:3000"

    class Config:
        env_prefix = "GC_"


settings = Settings()
