"""Data models for network debug command."""

from typing import Any

from deepctl_core import BaseResult
from pydantic import BaseModel, Field


class EndpointTestResult(BaseModel):
    """Result of testing a single endpoint."""

    name: str
    url: str
    reachable: bool
    status_code: int | None = None
    response_time_ms: float | None = None
    error: str | None = None
    ssl_valid: bool = True


class DNSResult(BaseModel):
    """DNS resolution result."""

    hostname: str
    resolved: bool
    ip_addresses: list[str] = Field(default_factory=list)
    resolution_time_ms: float | None = None
    error: str | None = None


class CertificateInfo(BaseModel):
    """Information about a single certificate in the chain."""

    subject: str
    issuer: str
    not_before: str | None = None
    not_after: str | None = None
    serial_number: str | None = None
    signature_algorithm: str | None = None
    is_self_signed: bool = False
    ocsp_urls: list[str] = Field(default_factory=list)
    ca_issuer_urls: list[str] = Field(default_factory=list)
    crl_distribution_points: list[str] = Field(default_factory=list)


class RevocationEndpointTest(BaseModel):
    """Result of testing a revocation/CA endpoint."""

    url: str
    endpoint_type: str  # 'ocsp', 'crl', or 'ca_issuer'
    accessible: bool
    status_code: int | None = None
    response_time_ms: float | None = None
    error: str | None = None


class TLSTestResult(BaseModel):
    """Result of TLS/SSL connectivity test."""

    hostname: str
    port: int
    connected: bool
    tls_version: str | None = None
    cipher_suite: str | None = None
    certificate_chain: list[CertificateInfo] = Field(default_factory=list)
    revocation_endpoints: list[RevocationEndpointTest] = Field(
        default_factory=list
    )
    chain_valid: bool = False
    chain_errors: list[str] = Field(default_factory=list)
    raw_openssl_output: str | None = None


class PythonRequestsTest(BaseModel):
    """Result of testing with Python requests library."""

    url: str
    success: bool
    status_code: int | None = None
    response_time_ms: float | None = None
    ssl_verify_enabled: bool = True
    error: str | None = None
    ssl_info: dict[str, Any] | None = None


class CommandExecutionResult(BaseModel):
    """Result of executing a system command."""

    command: str
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: float | None = None


class NetworkDebugResult(BaseResult):
    """Result from network debug command execution."""

    dns_results: dict[str, DNSResult] = Field(default_factory=dict)
    endpoint_results: list[EndpointTestResult] = Field(default_factory=list)
    tls_test_results: dict[str, TLSTestResult] = Field(default_factory=dict)
    python_requests_tests: list[PythonRequestsTest] = Field(
        default_factory=list
    )
    command_results: list[CommandExecutionResult] = Field(default_factory=list)
    proxy_detected: bool = False
    proxy_settings: dict[str, str] | None = None
    network_issues_detected: bool = False
    recommendations: list[str] = Field(default_factory=list)
    environment_info: dict[str, Any] = Field(default_factory=dict)


class DeepgramEndpoint(BaseModel):
    """Information about a Deepgram API endpoint."""

    name: str
    url: str
    description: str
    protocol: str = "https"  # https or wss
    region: str | None = None
