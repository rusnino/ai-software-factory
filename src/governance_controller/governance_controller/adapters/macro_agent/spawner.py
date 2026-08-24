"""Lifecycle helper for the macro-agent backend."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog

from governance_controller.adapters.macro_agent.client import MacroAgentClient
from governance_controller.adapters.macro_agent.local_service import (
    LocalMacroAgentService,
)
from governance_controller.config import settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def macro_agent_backend() -> AsyncGenerator[MacroAgentClient]:
    """Yield a MacroAgentClient, optionally starting a local service first.

    When ``settings.macro_agent_start_local`` is true, this context manager
    starts the local macro-agent service scaffold and yields a client pointing
    at it. Otherwise it yields a client pointing at ``settings.macro_agent_base_url``.
    """
    if settings.macro_agent_start_local:
        async with LocalMacroAgentService() as service:
            logger.info(
                "using_local_macro_agent_service",
                base_url=service.base_url,
            )
            yield MacroAgentClient(base_url=service.base_url)
    else:
        yield MacroAgentClient()
