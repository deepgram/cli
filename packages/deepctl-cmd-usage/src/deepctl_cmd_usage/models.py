"""Models for usage command."""

from typing import List
from pydantic import BaseModel, Field
from deepctl_core import BaseResult


class UsageBucket(BaseModel):
    start: str  # ISO date
    end: str
    hours: float


class UsageResult(BaseResult):
    project_id: str
    buckets: List[UsageBucket] = Field(default_factory=list)
    total_hours: float = 0.0
