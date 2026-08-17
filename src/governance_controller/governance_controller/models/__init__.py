from sqlmodel import SQLModel

from governance_controller.models.task import Task

__all__ = ["Base", "Task"]

Base = SQLModel
