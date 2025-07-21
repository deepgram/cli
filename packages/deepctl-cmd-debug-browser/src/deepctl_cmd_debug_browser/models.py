"""Data models for browser debug command."""

from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field
from datetime import datetime

from deepctl_core import BaseResult


class BrowserDebugResult(BaseResult):
    """Result from browser debug command execution."""

    url: str
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    headers: Optional[Dict[str, str]] = None
    body_preview: Optional[str] = None
    ssl_info: Optional[Dict[str, Any]] = None
    error_details: Optional[str] = None


class BrowserConnectionInfo(BaseModel):
    """Information about a browser connection test."""

    url: str
    method: str = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout: int = 30
    follow_redirects: bool = True
    verify_ssl: bool = True
