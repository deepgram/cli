"""Data models for browser debug command."""

from datetime import datetime
from enum import Enum
from typing import Any

from deepctl_core import BaseResult
from pydantic import BaseModel, Field, field_serializer


class MessageType(str, Enum):
    """Types of messages from the browser debugger."""

    CAPABILITY_CHECK = "capability_check"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"
    COMPLETE = "complete"


class BrowserCapability(BaseModel):
    """Individual browser capability check result."""

    name: str
    supported: bool
    version: str | None = None
    details: str | None = None
    required: bool = True


class BrowserCapabilities(BaseModel):
    """All browser capability check results."""

    web_audio_api: BrowserCapability
    audio_context: BrowserCapability
    audio_worklet: BrowserCapability
    websocket_api: BrowserCapability
    fetch_api: BrowserCapability
    es6_features: BrowserCapability
    dom_apis: BrowserCapability
    console_api: BrowserCapability
    timer_apis: BrowserCapability
    secure_context: BrowserCapability
    user_agent: str
    overall_compatible: bool


class WebSocketMessage(BaseModel):
    """Message received from the browser via WebSocket."""

    type: MessageType
    timestamp: datetime = Field(default_factory=datetime.now)
    data: dict[str, Any]
    message: str | None = None

    @field_serializer("timestamp")
    def serialize_timestamp(self, timestamp: datetime) -> str:
        """Serialize datetime to ISO format string."""
        return timestamp.isoformat()


class BrowserDebugResult(BaseResult):
    """Result from browser debug command execution."""

    status: str = "success"
    port: int
    capabilities: BrowserCapabilities | None = None
    messages: list[WebSocketMessage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    browser_opened: bool = False
