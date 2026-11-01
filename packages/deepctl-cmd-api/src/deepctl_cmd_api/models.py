"""Data models for API command."""

from typing import Any

from deepctl_core import BaseResult


class ApiResult(BaseResult):
    """Result from API command execution."""

    method: str = ""
    url: str = ""
    status_code: int = 0
    response_body: Any = None
    elapsed_ms: float | None = None


class ApiErrorResult(ApiResult):
    """`dg api` error that also carries a machine-readable code and a hint.

    A subclass so every other `dg api` payload keeps its existing keys.
    """

    code: str
    hint: str
