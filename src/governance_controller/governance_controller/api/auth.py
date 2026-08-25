"""Shared API authentication dependencies for sensitive Controller endpoints."""

import hmac

from fastapi import Header

from governance_controller.config import settings


class ControllerAuthError(Exception):
    """Raised when a sensitive Controller API request fails authentication."""


def require_controller_secret(
    x_controller_secret: str | None = Header(
        default=None, alias="X-Controller-Secret"
    ),
) -> None:
    """Validate the shared secret for sensitive Controller API mutations.

    The endpoint is closed by default. If ``controller_api_secret`` is not
    configured, or if the request presents the wrong secret, the request is
    rejected.
    """
    configured = settings.controller_api_secret
    if not configured or not hmac.compare_digest(
        x_controller_secret or "", configured
    ):
        raise ControllerAuthError("Invalid or missing Controller API secret")
