"""Pydantic schemas for the opentasks runtime DAG."""

from typing import Any

from pydantic import BaseModel, Field


class OpentasksTask(BaseModel):
    """A single opentasks runtime task node."""

    id: str
    plane_task_id: str
    objective: str
    acceptance: list[str] = Field(default_factory=list)
    harness: str = "opencode"
    role: str = "worker"
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpentasksDAG(BaseModel):
    """Materialized runtime DAG ready for opentasks ingestion."""

    project_id: str
    tasks: list[OpentasksTask] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
