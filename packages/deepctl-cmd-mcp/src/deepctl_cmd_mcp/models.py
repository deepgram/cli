"""Models for the MCP command."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TransportType(str, Enum):
    """MCP transports. Only ``STDIO`` is served.

    ``SSE`` stays so code that names it still imports; ``dg mcp`` refuses it.
    """

    STDIO = "stdio"
    SSE = "sse"


class MCPServerResult(BaseModel):
    """Result of MCP server operation."""

    status: str
    message: str
    transport: Optional[TransportType] = None
    port: Optional[int] = None
    host: Optional[str] = None
