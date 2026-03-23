"""Models for requests command."""

from __future__ import annotations

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class RequestInfo(BaseModel):
    request_id: str = ""
    created: str = ""
    path: str = ""
    method: str = ""
    status: str = ""
    duration: float = 0.0


class RequestsResult(BaseResult):
    requests: list[RequestInfo] = Field(default_factory=list)
    count: int = 0
