"""Models for projects command."""

from typing import List, Optional
from pydantic import BaseModel, Field
from deepctl_core import BaseResult


class ProjectInfo(BaseModel):
    project_id: str
    name: str
    company: Optional[str] = None


class ProjectsResult(BaseResult):
    projects: List[ProjectInfo] = Field(default_factory=list)
    count: int = 0


class ProjectList(BaseResult):
    projects: List[ProjectInfo] = Field(default_factory=list)
    count: int = 0
