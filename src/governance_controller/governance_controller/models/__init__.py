from sqlmodel import SQLModel

from governance_controller.models.approval import Approval
from governance_controller.models.task import Task

__all__ = ["Base", "Task", "Approval"]

Base = SQLModel
