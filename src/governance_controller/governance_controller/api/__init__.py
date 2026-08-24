"""REST API routers for the Governance Controller."""

from governance_controller.api import approvals as approvals
from governance_controller.api import audit as audit
from governance_controller.api import executions as executions
from governance_controller.api import health as health
from governance_controller.api import intake as intake
from governance_controller.api import tasks as tasks
from governance_controller.api import webhooks as webhooks

__all__ = [
    "approvals",
    "audit",
    "executions",
    "health",
    "intake",
    "tasks",
    "webhooks",
]
