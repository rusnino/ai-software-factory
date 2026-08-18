"""REST API routers for the Governance Controller."""

from governance_controller.api import approvals as approvals
from governance_controller.api import executions as executions
from governance_controller.api import tasks as tasks

__all__ = ["approvals", "executions", "tasks"]
