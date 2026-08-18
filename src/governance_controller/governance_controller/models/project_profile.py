"""ProjectProfile SQLModel entity."""

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, SQLModel


class ProjectProfileModel(SQLModel, table=True):
    """A persisted project profile used by policy and approval endpoints."""

    __tablename__ = "project_profiles"

    project_id: str = Field(primary_key=True)
    profile_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("profile_json", JSON()),
    )
