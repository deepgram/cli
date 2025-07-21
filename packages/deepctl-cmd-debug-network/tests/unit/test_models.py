"""Tests for network debug command models."""

import pytest
from datetime import datetime
from deepctl_cmd_debug_network.models import (
    EndpointTestResult,
    DNSResult,
    CertificateInfo,
    RevocationEndpointTest,
    TLSTestResult,
    PythonRequestsTest,
    CommandExecutionResult,
    NetworkDebugResult,
    DeepgramEndpoint
)


class TestEndpointTestResult:
    """Test cases for EndpointTestResult model."""

    def test_create_endpoint_test_result(self):
        """Test creating an EndpointTestResult."""
        result = EndpointTestResult(
            name="HTTPS",
            url="https://api.deepgram.com",
            reachable=True,
            status_code=200,
            response_time_ms=123.45,
            ssl_valid=True
        )

        assert result.name == "HTTPS"
        assert result.url == "https://api.deepgram.com"
        assert result.reachable is True
        assert result.status_code == 200
        assert result.response_time_ms == 123.45
        assert result.ssl_valid is True
        assert result.error is None

    def test_endpoint_test_result_with_error(self):
        """Test EndpointTestResult with error."""
        result = EndpointTestResult(
            name="HTTPS",
            url="https://api.deepgram.com",
            reachable=False,
            error="Connection timeout",
            ssl_valid=False
        )

        assert result.reachable is False
        assert result.error == "Connection timeout"
        assert result.ssl_valid is False
        assert result.status_code is None


class TestDNSResult:
    """Test cases for DNSResult model."""

    def test_create_dns_result_success(self):
        """Test creating a successful DNS result."""
        result = DNSResult(
            hostname="api.deepgram.com",
            resolved=True,
            ip_addresses=["1.2.3.4", "5.6.7.8"],
            resolution_time_ms=50.25
        )

        assert result.hostname == "api.deepgram.com"
        assert result.resolved is True
        assert len(result.ip_addresses) == 2
        assert "1.2.3.4" in result.ip_addresses
        assert result.resolution_time_ms == 50.25
        assert result.error is None

    def test_create_dns_result_failure(self):
        """Test creating a failed DNS result."""
        result = DNSResult(
            hostname="invalid.example.com",
            resolved=False,
            error="Name resolution failed"
        )

        assert result.resolved is False
        assert result.error == "Name resolution failed"
        assert len(result.ip_addresses) == 0


class TestCertificateInfo:
    """Test cases for CertificateInfo model."""

    def test_create_certificate_info(self):
        """Test creating CertificateInfo."""
        cert = CertificateInfo(
            subject="CN=api.deepgram.com",
            issuer="C=US, O=Let's Encrypt, CN=R11",
            not_before="2025-05-01T13:45:25Z",
            not_after="2025-07-30T13:45:24Z",
            serial_number="123456789",
            signature_algorithm="sha256WithRSAEncryption",
            is_self_signed=False,
            ocsp_urls=["http://r11.o.lencr.org"],
            ca_issuer_urls=["http://r11.i.lencr.org/"],
            crl_distribution_points=["http://r11.c.lencr.org/31.crl"]
        )

        assert cert.subject == "CN=api.deepgram.com"
        assert cert.issuer == "C=US, O=Let's Encrypt, CN=R11"
        assert cert.is_self_signed is False
        assert len(cert.ocsp_urls) == 1
        assert len(cert.ca_issuer_urls) == 1
        assert len(cert.crl_distribution_points) == 1

    def test_certificate_info_defaults(self):
        """Test CertificateInfo with default values."""
        cert = CertificateInfo(
            subject="CN=test",
            issuer="CN=test"
        )

        assert cert.subject == "CN=test"
        assert cert.issuer == "CN=test"
        assert cert.is_self_signed is False
        assert len(cert.ocsp_urls) == 0
        assert len(cert.ca_issuer_urls) == 0
        assert len(cert.crl_distribution_points) == 0


class TestRevocationEndpointTest:
    """Test cases for RevocationEndpointTest model."""

    def test_create_revocation_endpoint_test(self):
        """Test creating RevocationEndpointTest."""
        endpoint = RevocationEndpointTest(
            url="http://r11.o.lencr.org",
            endpoint_type="ocsp",
            accessible=True,
            status_code=200,
            response_time_ms=100.5
        )

        assert endpoint.url == "http://r11.o.lencr.org"
        assert endpoint.endpoint_type == "ocsp"
        assert endpoint.accessible is True
        assert endpoint.status_code == 200
        assert endpoint.response_time_ms == 100.5
        assert endpoint.error is None

    def test_revocation_endpoint_test_blocked(self):
        """Test RevocationEndpointTest for blocked endpoint."""
        endpoint = RevocationEndpointTest(
            url="http://r11.c.lencr.org/31.crl",
            endpoint_type="crl",
            accessible=False,
            error="Connection timeout"
        )

        assert endpoint.endpoint_type == "crl"
        assert endpoint.accessible is False
        assert endpoint.error == "Connection timeout"
        assert endpoint.status_code is None


class TestTLSTestResult:
    """Test cases for TLSTestResult model."""

    def test_create_tls_test_result(self):
        """Test creating TLSTestResult."""
        result = TLSTestResult(
            hostname="api.deepgram.com",
            port=443,
            connected=True,
            tls_version="TLSv1.3",
            cipher_suite="TLS_AES_256_GCM_SHA384",
            chain_valid=True
        )

        assert result.hostname == "api.deepgram.com"
        assert result.port == 443
        assert result.connected is True
        assert result.tls_version == "TLSv1.3"
        assert result.cipher_suite == "TLS_AES_256_GCM_SHA384"
        assert result.chain_valid is True
        assert len(result.certificate_chain) == 0
        assert len(result.revocation_endpoints) == 0
        assert len(result.chain_errors) == 0

    def test_tls_test_result_with_certificates(self):
        """Test TLSTestResult with certificate chain."""
        result = TLSTestResult(
            hostname="api.deepgram.com",
            port=443,
            connected=True
        )

        cert1 = CertificateInfo(
            subject="CN=api.deepgram.com",
            issuer="CN=Intermediate"
        )
        cert2 = CertificateInfo(
            subject="CN=Intermediate",
            issuer="CN=Root"
        )

        result.certificate_chain.append(cert1)
        result.certificate_chain.append(cert2)

        assert len(result.certificate_chain) == 2
        assert result.certificate_chain[0].subject == "CN=api.deepgram.com"
        assert result.certificate_chain[1].subject == "CN=Intermediate"


class TestPythonRequestsTest:
    """Test cases for PythonRequestsTest model."""

    def test_create_python_requests_test(self):
        """Test creating PythonRequestsTest."""
        test = PythonRequestsTest(
            url="https://api.deepgram.com/",
            success=True,
            status_code=404,
            response_time_ms=250.75,
            ssl_verify_enabled=True
        )

        assert test.url == "https://api.deepgram.com/"
        assert test.success is True
        assert test.status_code == 404
        assert test.response_time_ms == 250.75
        assert test.ssl_verify_enabled is True
        assert test.error is None

    def test_python_requests_test_with_error(self):
        """Test PythonRequestsTest with error."""
        test = PythonRequestsTest(
            url="https://api.deepgram.com/",
            success=False,
            ssl_verify_enabled=True,
            error="SSL certificate verification failed"
        )

        assert test.success is False
        assert test.error == "SSL certificate verification failed"
        assert test.status_code is None


class TestCommandExecutionResult:
    """Test cases for CommandExecutionResult model."""

    def test_create_command_execution_result(self):
        """Test creating CommandExecutionResult."""
        result = CommandExecutionResult(
            command="ping -c 3 api.deepgram.com",
            success=True,
            exit_code=0,
            stdout="3 packets transmitted, 3 received",
            stderr="",
            execution_time_ms=2150.5
        )

        assert result.command == "ping -c 3 api.deepgram.com"
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "3 packets transmitted, 3 received"
        assert result.stderr == ""
        assert result.execution_time_ms == 2150.5

    def test_command_execution_result_failure(self):
        """Test CommandExecutionResult for failed command."""
        result = CommandExecutionResult(
            command="invalid-command",
            success=False,
            exit_code=127,
            stdout="",
            stderr="command not found"
        )

        assert result.success is False
        assert result.exit_code == 127
        assert result.stderr == "command not found"


class TestNetworkDebugResult:
    """Test cases for NetworkDebugResult model."""

    def test_create_network_debug_result(self):
        """Test creating NetworkDebugResult."""
        result = NetworkDebugResult(status="success")

        assert result.status == "success"
        assert isinstance(result.dns_results, dict)
        assert isinstance(result.endpoint_results, list)
        assert isinstance(result.tls_test_results, dict)
        assert isinstance(result.python_requests_tests, list)
        assert isinstance(result.command_results, list)
        assert result.proxy_detected is False
        assert result.proxy_settings is None
        assert result.network_issues_detected is False
        assert isinstance(result.recommendations, list)
        assert isinstance(result.environment_info, dict)

    def test_network_debug_result_with_data(self):
        """Test NetworkDebugResult with populated data."""
        result = NetworkDebugResult(status="success")

        # Add DNS result
        dns_result = DNSResult(
            hostname="api.deepgram.com",
            resolved=True,
            ip_addresses=["1.2.3.4"]
        )
        result.dns_results["api.deepgram.com"] = dns_result

        # Add endpoint result
        endpoint_result = EndpointTestResult(
            name="HTTPS",
            url="https://api.deepgram.com",
            reachable=True
        )
        result.endpoint_results.append(endpoint_result)

        # Add recommendation
        result.recommendations.append("All tests passed")

        # Set proxy detected
        result.proxy_detected = True
        result.proxy_settings = {"https_proxy": "http://proxy:8080"}

        assert len(result.dns_results) == 1
        assert len(result.endpoint_results) == 1
        assert len(result.recommendations) == 1
        assert result.proxy_detected is True
        assert result.proxy_settings["https_proxy"] == "http://proxy:8080"

    def test_network_debug_result_serialization(self):
        """Test NetworkDebugResult can be serialized."""
        result = NetworkDebugResult(status="success")
        result.dns_results["test.com"] = DNSResult(
            hostname="test.com",
            resolved=True,
            ip_addresses=["1.2.3.4"]
        )

        # Should be able to convert to dict
        data = result.model_dump()

        assert data["status"] == "success"
        assert "test.com" in data["dns_results"]
        assert data["dns_results"]["test.com"]["resolved"] is True


class TestDeepgramEndpoint:
    """Test cases for DeepgramEndpoint model."""

    def test_create_deepgram_endpoint(self):
        """Test creating DeepgramEndpoint."""
        endpoint = DeepgramEndpoint(
            name="api",
            url="https://api.deepgram.com",
            description="Main API endpoint",
            protocol="https",
            region="us-east-1"
        )

        assert endpoint.name == "api"
        assert endpoint.url == "https://api.deepgram.com"
        assert endpoint.description == "Main API endpoint"
        assert endpoint.protocol == "https"
        assert endpoint.region == "us-east-1"

    def test_deepgram_endpoint_defaults(self):
        """Test DeepgramEndpoint with default values."""
        endpoint = DeepgramEndpoint(
            name="websocket",
            url="wss://api.deepgram.com",
            description="WebSocket endpoint"
        )

        assert endpoint.name == "websocket"
        assert endpoint.protocol == "https"  # Default value
        assert endpoint.region is None
