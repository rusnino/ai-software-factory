"""Application configuration and settings."""

import structlog
from pydantic import field_validator
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
    # Shared secret sent to macro-agent service in X-Macro-Agent-Secret header.
    # Leave empty only for local/dev where the service is not exposed.
    macro_agent_api_secret: str = ""

    # If true, the Controller will spawn macro_agent_service as a subprocess
    # rather than talking to a separately deployed service.
    macro_agent_start_local: bool = False
    macro_agent_local_port: int = 3000
    macro_agent_local_host: str = "127.0.0.1"


    # Plane CE integration. Leave empty to run with the in-memory Plane adapter.
    plane_base_url: str = ""
    plane_api_token: str = ""
    plane_workspace_slug: str = ""
    plane_project_id: str = ""

    log_level: str = "info"

    # Telegram webhook authentication. The secret token is sent by Telegram in
    # the ``X-Telegram-Bot-Api-Secret-Token`` header when webhooks are
    # configured with a secret_token. Set to a non-empty value and route the
    # header to ``TelegramAdapter`` to validate that updates come from Telegram.
    telegram_webhook_secret_token: str = ""

    # Plane webhook authentication. Plane CE does not natively sign webhooks,
    # so we require a shared secret in the ``X-Plane-Webhook-Secret`` header.
    # Leave empty ONLY for local/dev where the endpoint is not exposed.
    plane_webhook_secret: str = ""
    # Comma-separated list of Plane member emails that are allowed to originate
    # state-change webhooks. If empty, no actor is trusted and every webhook is
    # rejected (fail-closed). Use Plane member email addresses because they are
    # stable, human-verified identifiers in Plane CE.
    plane_webhook_allowed_actors: str = ""

    # Generic intake webhook authentication. Email and generic idea intake
    # endpoints require this shared secret in the ``X-Intake-Secret`` header.
    # Leave empty ONLY for local/dev where the endpoints are not exposed.
    intake_secret: str = ""

    # Optional Open Policy Agent (OPA) backend. When configured, the embedded
    # PolicyEngine delegates policy evaluation to OPA. Controller keeps the
    # state machine, approvals, and audit log authoritative.
    opa_base_url: str = ""
    opa_timeout_seconds: float = 5.0
    opa_policy_path: str = "governance/approve"
    # Optional bearer token for authenticating to OPA. Leave empty when OPA is
    # reached via a trusted sidecar/network path.
    opa_api_token: str = ""

    # Shared secret for authenticating routine macro-agent Event Bridge
    # callbacks to the Controller's POST /events endpoint. The caller must send
    # the secret in the X-Event-Bridge-Secret header. Leave empty ONLY in local
    # dev where the endpoint is not exposed. This credential is NOT sufficient
    # for conflict:resolved events that unblock a BLOCKED task; those also
    # require X-Human-Admin-Secret.
    event_bridge_secret: str = ""

    # Separate human-scoped secret required for conflict:resolved events that
    # unblock a BLOCKED task. This event type deliberately overrides the
    # "human intervention required" rule; it must therefore prove that a human
    # actor initiated the call. Leave empty ONLY in local dev where the endpoint
    # is not exposed. If unset in production, conflict:resolved is rejected.
    event_bridge_human_secret: str = ""

    # Intake rate limiting: maximum draft-creating submissions per sender per
    # minute. Set to 0 to disable rate limiting.
    intake_rate_limit_per_minute: int = 10

    # Global API rate limiting: maximum requests per originating IP per minute
    # for all Controller endpoints except /health. Set to 0 to disable.
    rate_limit_per_minute: int = 120
    # Maximum distinct source IPs tracked simultaneously by the in-memory rate
    # limiter. Prevents unbounded growth when clients vary forwarded headers.
    rate_limit_max_ips: int = 10_000

    # Comma-separated list of actors that are allowed to grant EXECUTION and
    # MERGE approvals. Must be set in production; the empty default denies all
    # sensitive approvals so the Controller fails closed.
    admins: str = ""

    # Shared secret for authenticating sensitive Controller API mutations
    # (POST /tasks, POST /approvals, etc.). Callers must send the secret in the
    # X-Controller-Secret header. Leave empty ONLY in local dev where the API is
    # not exposed.
    controller_api_secret: str = ""

    # Runtime DAG materialization limits. These bound the work an agent can be
    # handed in a single execution and prevent unbounded Plane API fan-out.
    opentasks_max_dag_size: int = 1000
    opentasks_materializer_concurrency: int = 10

    @field_validator("database_pool_size", "database_max_overflow")
    @classmethod
    def _positive_pool_setting(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("pool size and max_overflow must be positive")
        return value

    @field_validator("database_pool_timeout")
    @classmethod
    def _non_negative_pool_timeout(cls, value: int) -> int:
        if value < 0:
            raise ValueError("pool timeout must be non-negative")
        return value

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in NAME_TO_LEVEL:
            raise ValueError(f"unknown log level: {value}")
        return normalized


def configure_logging(log_level: str) -> None:
    """Configure structlog using the provided log level."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            NAME_TO_LEVEL.get(log_level.lower(), 20)
        ),
    )


settings = Settings()
configure_logging(settings.log_level)
