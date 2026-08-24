"""Configuration for the macro-agent service scaffold."""

import os


class Config:
    """Simple environment-based configuration."""

    def __init__(self) -> None:
        self.host: str = os.environ.get("MACRO_AGENT_SERVICE_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("MACRO_AGENT_SERVICE_PORT", "3000"))
        # Shared secret for Controller → service requests. Leave empty only in
        # local dev where the service is not exposed.
        self.api_secret: str = os.environ.get(
            "MACRO_AGENT_SERVICE_API_SECRET", ""
        )


config = Config()
