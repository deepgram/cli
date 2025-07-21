"""Data models for network debug command."""

from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime

from deepctl_core import BaseResult


class EndpointTestResult(BaseModel):
    """Result of testing a single endpoint."""

    name: str
    url: str
    reachable: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    error: Optional[str] = None
    ssl_valid: bool = True


class DNSResult(BaseModel):
    """DNS resolution result."""

    hostname: str
    resolved: bool
    ip_addresses: List[str] = Field(default_factory=list)
    resolution_time_ms: Optional[float] = None
    error: Optional[str] = None


class NetworkDebugResult(BaseResult):
    """Result from network debug command execution."""

    dns_results: Dict[str, DNSResult] = Field(default_factory=dict)
    endpoint_results: List[EndpointTestResult] = Field(default_factory=list)
    proxy_detected: bool = False
    proxy_settings: Optional[Dict[str, str]] = None
    network_issues_detected: bool = False
    recommendations: List[str] = Field(default_factory=list)


class DeepgramEndpoint(BaseModel):
    """Information about a Deepgram API endpoint."""

    name: str
    url: str
    description: str
    protocol: str = "https"  # https or wss
    region: Optional[str] = None
