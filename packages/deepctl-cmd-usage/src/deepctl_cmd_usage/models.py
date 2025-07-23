"""Models for usage command."""

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class UsageBucket(BaseModel):
    start: str  # ISO date
    end: str
    hours: float


class UsageResult(BaseResult):
    project_id: str
    buckets: list[UsageBucket] = Field(default_factory=list)
    total_hours: float = 0.0
