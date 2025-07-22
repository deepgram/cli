"""Data models for browser debug command."""

from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field, field_serializer
from datetime import datetime
from enum import Enum

from deepctl_core import BaseResult


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
    version: Optional[str] = None
    details: Optional[str] = None
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
    data: Dict[str, Any]
    message: Optional[str] = None

    @field_serializer("timestamp")
    def serialize_timestamp(self, timestamp: datetime) -> str:
        """Serialize datetime to ISO format string."""
        return timestamp.isoformat()


class BrowserDebugResult(BaseResult):
    """Result from browser debug command execution."""

    status: str = "success"
    port: int
    capabilities: Optional[BrowserCapabilities] = None
    messages: List[WebSocketMessage] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    duration_seconds: Optional[float] = None
    browser_opened: bool = False
