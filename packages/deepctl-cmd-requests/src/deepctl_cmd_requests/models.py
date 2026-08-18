"""Models for requests command."""

from __future__ import annotations

from typing import Any

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


class RequestDetailResult(BaseResult):
    # Returned by `--show`; carries the full request detail so json/yaml
    # output is useful instead of a bare success message. Kept separate from
    # RequestsResult so single-request output doesn't emit an empty
    # `requests: []` / `count: 0` alongside the detail.
    detail: dict[str, Any] = Field(default_factory=dict)
