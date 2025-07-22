"""Data models for network debug command."""

from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field

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


class CertificateInfo(BaseModel):
    """Information about a single certificate in the chain."""

    subject: str
    issuer: str
    not_before: Optional[str] = None
    not_after: Optional[str] = None
    serial_number: Optional[str] = None
    signature_algorithm: Optional[str] = None
    is_self_signed: bool = False
    ocsp_urls: List[str] = Field(default_factory=list)
    ca_issuer_urls: List[str] = Field(default_factory=list)
    crl_distribution_points: List[str] = Field(default_factory=list)


class RevocationEndpointTest(BaseModel):
    """Result of testing a revocation/CA endpoint."""

    url: str
    endpoint_type: str  # 'ocsp', 'crl', or 'ca_issuer'
    accessible: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    error: Optional[str] = None


class TLSTestResult(BaseModel):
    """Result of TLS/SSL connectivity test."""

    hostname: str
    port: int
    connected: bool
    tls_version: Optional[str] = None
    cipher_suite: Optional[str] = None
    certificate_chain: List[CertificateInfo] = Field(default_factory=list)
    revocation_endpoints: List[RevocationEndpointTest] = Field(
        default_factory=list
    )
    chain_valid: bool = False
    chain_errors: List[str] = Field(default_factory=list)
    raw_openssl_output: Optional[str] = None


class PythonRequestsTest(BaseModel):
    """Result of testing with Python requests library."""

    url: str
    success: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    ssl_verify_enabled: bool = True
    error: Optional[str] = None
    ssl_info: Optional[Dict[str, Any]] = None


class CommandExecutionResult(BaseModel):
    """Result of executing a system command."""

    command: str
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: Optional[float] = None


class NetworkDebugResult(BaseResult):
    """Result from network debug command execution."""

    dns_results: Dict[str, DNSResult] = Field(default_factory=dict)
    endpoint_results: List[EndpointTestResult] = Field(default_factory=list)
    tls_test_results: Dict[str, TLSTestResult] = Field(default_factory=dict)
    python_requests_tests: List[PythonRequestsTest] = Field(
        default_factory=list
    )
    command_results: List[CommandExecutionResult] = Field(default_factory=list)
    proxy_detected: bool = False
    proxy_settings: Optional[Dict[str, str]] = None
    network_issues_detected: bool = False
    recommendations: List[str] = Field(default_factory=list)
    environment_info: Dict[str, Any] = Field(default_factory=dict)


class DeepgramEndpoint(BaseModel):
    """Information about a Deepgram API endpoint."""

    name: str
    url: str
    description: str
    protocol: str = "https"  # https or wss
    region: Optional[str] = None
