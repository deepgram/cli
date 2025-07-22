"""Models for the MCP command."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TransportType(str, Enum):
    """Transport types for MCP server."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLEHTTP = "streamablehttp"


class MCPServerResult(BaseModel):
    """Result of MCP server operation."""

    status: str
    message: str
    transport: Optional[TransportType] = None
    port: Optional[int] = None
    host: Optional[str] = None
