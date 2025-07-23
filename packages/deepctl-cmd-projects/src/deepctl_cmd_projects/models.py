"""Models for projects command."""

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class ProjectInfo(BaseModel):
    project_id: str
    name: str
    company: str | None = None


class ProjectsResult(BaseResult):
    projects: list[ProjectInfo] = Field(default_factory=list)
    count: int = 0


class ProjectList(BaseResult):
    projects: list[ProjectInfo] = Field(default_factory=list)
    count: int = 0
