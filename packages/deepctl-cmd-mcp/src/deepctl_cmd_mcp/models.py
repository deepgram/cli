"""Models for the MCP command."""

from typing import Optional

from deepgram_mcp import TransportType as TransportType
from pydantic import BaseModel


class MCPServerResult(BaseModel):
    """Result of MCP server operation."""

    status: str
    message: str
    transport: Optional[TransportType] = None
    port: Optional[int] = None
    host: Optional[str] = None
