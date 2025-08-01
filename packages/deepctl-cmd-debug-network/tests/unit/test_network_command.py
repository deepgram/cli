"""Tests for the network debug command."""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
import subprocess
import socket
import requests
import json
from datetime import datetime

from deepctl_cmd_debug_network import NetworkCommand
from deepctl_cmd_debug_network.models import (
    NetworkDebugResult,
    DNSResult,
    TLSTestResult,
    CertificateInfo,
    RevocationEndpointTest,
    PythonRequestsTest,
    CommandExecutionResult,
    EndpointTestResult,
)
from deepctl_core import Config, AuthManager, DeepgramClient


@pytest.fixture
def network_command():
    """Create network command instance."""
    return NetworkCommand()


@pytest.fixture
def mock_config():
    """Create mock config."""
    config = Mock(spec=Config)
    config.get.return_value = "json"
    return config


@pytest.fixture
def mock_auth_manager():
    """Create mock auth manager."""
    return Mock(spec=AuthManager)


@pytest.fixture
def mock_client():
    """Create mock Deepgram client."""
    return Mock(spec=DeepgramClient)


class TestNetworkCommand:
    """Test cases for NetworkCommand."""

    def test_command_metadata(self, network_command):
        """Test command has correct metadata."""
        assert network_command.name == "network"
        assert (
            network_command.help
            == "Debug network connectivity issues with Deepgram services"
        )
        assert network_command.short_help == "Debug network issues"
        assert network_command.requires_auth is False
        assert network_command.requires_project is False
        assert network_command.ci_friendly is True

    def test_get_arguments(self, network_command):
        """Test command arguments are properly defined."""
        args = network_command.get_arguments()
        assert len(args) == 7

        # Check endpoint argument
        endpoint_arg = next(
            arg for arg in args if "--endpoint" in arg["names"]
        )
        assert (
            endpoint_arg["help"]
            == "Specific endpoint to test (default: api.deepgram.com)"
        )
        assert endpoint_arg["type"] == str

        # Check verbose flag
        verbose_arg = next(arg for arg in args if "--verbose" in arg["names"])
        assert verbose_arg["help"] == "Show detailed diagnostic information"
        assert verbose_arg["is_flag"] is True

        # Check skip-commands flag
        skip_arg = next(
            arg for arg in args if "--skip-commands" in arg["names"]
        )
        assert (
            skip_arg["help"]
            == "Skip system command execution (openssl, curl, etc.)"
        )
        assert skip_arg["is_flag"] is True

        # Check timeout argument
        timeout_arg = next(arg for arg in args if "--timeout" in arg["names"])
        assert (
            timeout_arg["help"]
            == "Timeout in seconds for network operations (default: 10)"
        )
        assert timeout_arg["type"] == int
        assert timeout_arg["default"] == 10

        # Check simulate-blocked-crl flag
        simulate_arg = next(
            arg for arg in args if "--simulate-blocked-crl" in arg["names"]
        )
        assert (
            simulate_arg["help"]
            == "Simulate blocked CRL/OCSP endpoints (for testing)"
        )
        assert simulate_arg["is_flag"] is True

        # Check save-report argument
        save_arg = next(arg for arg in args if "--save-report" in arg["names"])
        assert save_arg["help"] == "Save full diagnostic report to file"
        assert save_arg["type"] == str

        # Check test-websocket flag
        ws_arg = next(
            arg for arg in args if "--test-websocket" in arg["names"]
        )
        assert ws_arg["help"] == "Also test WebSocket connectivity (wss://)"
        assert ws_arg["is_flag"] is True

    @patch("socket.getaddrinfo")
    def test_dns_resolution_success(self, mock_getaddrinfo, network_command):
        """Test successful DNS resolution."""
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("5.6.7.8", 0)),
        ]

        result = NetworkDebugResult(status="success")
        network_command._test_dns_resolution("api.deepgram.com", result, 10)

        assert "api.deepgram.com" in result.dns_results
        dns_result = result.dns_results["api.deepgram.com"]
        assert dns_result.resolved is True
        assert "1.2.3.4" in dns_result.ip_addresses
        assert "5.6.7.8" in dns_result.ip_addresses
        assert dns_result.error is None

    @patch("socket.getaddrinfo")
    def test_dns_resolution_failure(self, mock_getaddrinfo, network_command):
        """Test failed DNS resolution."""
        mock_getaddrinfo.side_effect = socket.gaierror(
            "Name resolution failed"
        )

        result = NetworkDebugResult(status="success")
        network_command._test_dns_resolution("api.deepgram.com", result, 10)

        assert "api.deepgram.com" in result.dns_results
        dns_result = result.dns_results["api.deepgram.com"]
        assert dns_result.resolved is False
        assert dns_result.error == "Name resolution failed"
        assert result.network_issues_detected is True

    @patch("requests.get")
    def test_basic_connectivity_success(self, mock_get, network_command):
        """Test successful basic HTTPS connectivity."""
        mock_response = Mock()
        mock_response.status_code = 404  # Expected for root endpoint
        mock_get.return_value = mock_response

        result = NetworkDebugResult(status="success")
        network_command._test_basic_connectivity(
            "api.deepgram.com", result, 10
        )

        assert len(result.endpoint_results) == 1
        endpoint = result.endpoint_results[0]
        assert endpoint.name == "HTTPS"
        assert endpoint.url == "https://api.deepgram.com"
        assert endpoint.reachable is True
        assert endpoint.status_code == 404
        assert endpoint.ssl_valid is True

    @patch("requests.get")
    def test_basic_connectivity_ssl_error(self, mock_get, network_command):
        """Test basic connectivity with SSL error."""
        mock_get.side_effect = requests.exceptions.SSLError(
            "SSL certificate verification failed"
        )

        result = NetworkDebugResult(status="success")
        network_command._test_basic_connectivity(
            "api.deepgram.com", result, 10
        )

        assert len(result.endpoint_results) == 1
        endpoint = result.endpoint_results[0]
        assert endpoint.name == "HTTPS"
        assert endpoint.reachable is False
        assert endpoint.ssl_valid is False
        assert "SSL Error" in endpoint.error
        assert result.network_issues_detected is True

    @patch("subprocess.run")
    def test_analyze_tls_certificates(self, mock_run, network_command):
        """Test TLS certificate analysis."""
        # Mock openssl s_client output
        openssl_output = """-----BEGIN CERTIFICATE-----
MIITestCert1
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIITestCert2
-----END CERTIFICATE-----"""

        # Mock certificate details output
        cert_details = """subject=CN=api.deepgram.com
issuer=C=US, O=Let's Encrypt, CN=R11
Authority Information Access: 
    OCSP - URI:http://r11.o.lencr.org
    CA Issuers - URI:http://r11.i.lencr.org/
X509v3 CRL Distribution Points: 
    Full Name:
      URI:http://r11.c.lencr.org/31.crl"""

        mock_run.side_effect = [
            Mock(returncode=0, stdout=openssl_output.encode(), stderr=b""),
            Mock(returncode=0, stdout=cert_details.encode(), stderr=b""),
            Mock(returncode=0, stdout=cert_details.encode(), stderr=b""),
        ]

        result = NetworkDebugResult(status="success")
        with patch.object(network_command, "_test_revocation_endpoints"):
            network_command._analyze_tls_certificates(
                "api.deepgram.com", result, False, False
            )

        assert "api.deepgram.com" in result.tls_test_results
        tls_result = result.tls_test_results["api.deepgram.com"]
        assert tls_result.connected is True
        assert len(tls_result.certificate_chain) == 2

    def test_test_revocation_endpoints_success(self, network_command):
        """Test successful revocation endpoint testing."""
        tls_result = TLSTestResult(
            hostname="api.deepgram.com", port=443, connected=True
        )
        cert = CertificateInfo(
            subject="CN=api.deepgram.com",
            issuer="C=US, O=Let's Encrypt, CN=R11",
            ocsp_urls=["http://r11.o.lencr.org"],
            crl_distribution_points=["http://r11.c.lencr.org/31.crl"],
            ca_issuer_urls=["http://r11.i.lencr.org/"],
        )
        tls_result.certificate_chain.append(cert)

        result = NetworkDebugResult(status="success")

        with patch("requests.head") as mock_head:
            mock_head.return_value = Mock(status_code=200)
            network_command._test_revocation_endpoints(
                tls_result, result, False
            )

        assert len(tls_result.revocation_endpoints) == 3
        for endpoint in tls_result.revocation_endpoints:
            assert endpoint.accessible is True
            assert endpoint.status_code == 200

    def test_test_revocation_endpoints_simulated_block(self, network_command):
        """Test revocation endpoints with simulated blocking."""
        tls_result = TLSTestResult(
            hostname="api.deepgram.com", port=443, connected=True
        )
        cert = CertificateInfo(
            subject="CN=api.deepgram.com",
            issuer="C=US, O=Let's Encrypt, CN=R11",
            ocsp_urls=["http://r11.o.lencr.org"],
            crl_distribution_points=["http://r11.c.lencr.org/31.crl"],
            ca_issuer_urls=["http://r11.i.lencr.org/"],
        )
        tls_result.certificate_chain.append(cert)

        result = NetworkDebugResult(status="success")
        network_command._test_revocation_endpoints(
            tls_result, result, simulate_blocked_crl=True
        )

        # Check that CRL and OCSP endpoints are marked as blocked
        ocsp_endpoints = [
            e
            for e in tls_result.revocation_endpoints
            if e.endpoint_type == "ocsp"
        ]
        crl_endpoints = [
            e
            for e in tls_result.revocation_endpoints
            if e.endpoint_type == "crl"
        ]
        ca_endpoints = [
            e
            for e in tls_result.revocation_endpoints
            if e.endpoint_type == "ca_issuer"
        ]

        for endpoint in ocsp_endpoints + crl_endpoints:
            assert endpoint.accessible is False
            assert "simulated corporate firewall blocking" in endpoint.error

        for endpoint in ca_endpoints:
            assert (
                endpoint.accessible is True
            )  # CA issuer endpoints not blocked

    @patch("requests.get")
    def test_python_requests_tests(self, mock_get, network_command):
        """Test Python requests library connectivity tests."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = NetworkDebugResult(status="success")
        network_command._test_python_requests("api.deepgram.com", result, 10)

        assert len(result.python_requests_tests) == 2

        # Test with SSL verification
        test_with_verify = result.python_requests_tests[0]
        assert test_with_verify.ssl_verify_enabled is True
        assert test_with_verify.success is True
        assert test_with_verify.status_code == 404

        # Test without SSL verification
        test_without_verify = result.python_requests_tests[1]
        assert test_without_verify.ssl_verify_enabled is False
        assert test_without_verify.success is True
        assert test_without_verify.status_code == 404

    @patch("subprocess.run")
    def test_run_system_diagnostics(self, mock_run, network_command):
        """Test system diagnostic command execution."""
        with patch.object(
            network_command, "_command_exists", return_value=True
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout=b"PING api.deepgram.com: 3 packets transmitted, 3 received",
                stderr=b"",
            )

            result = NetworkDebugResult(status="success")
            network_command._run_system_diagnostics(
                "api.deepgram.com", result, False
            )

            # Should have executed multiple commands
            assert len(result.command_results) > 0
            for cmd_result in result.command_results:
                if cmd_result.success:
                    assert cmd_result.exit_code == 0
                    assert cmd_result.stdout != ""

    @patch("subprocess.run")
    def test_test_websocket_connectivity(self, mock_run, network_command):
        """Test WebSocket connectivity testing."""
        with patch.object(
            network_command, "_command_exists", return_value=True
        ):
            mock_run.return_value = Mock(
                returncode=0,
                stdout=b"HTTP/1.1 401 Unauthorized\r\n",
                stderr=b"",
            )

            result = NetworkDebugResult(status="success")
            network_command._test_websocket_connectivity(
                "api.deepgram.com", result, 10
            )

            # Check WebSocket test was added to endpoint results
            ws_results = [
                e for e in result.endpoint_results if e.name == "WebSocket"
            ]
            assert len(ws_results) == 1
            ws_result = ws_results[0]
            assert ws_result.url == "wss://api.deepgram.com/v1/listen"
            assert ws_result.reachable is True
            assert ws_result.status_code == 401  # Expected without auth

    def test_save_report(self, network_command, tmp_path):
        """Test saving diagnostic report to file."""
        result = NetworkDebugResult(status="success")
        result.dns_results["test.com"] = DNSResult(
            hostname="test.com", resolved=True, ip_addresses=["1.2.3.4"]
        )

        report_file = tmp_path / "test-report.json"
        network_command._save_report(result, str(report_file), verbose=True)

        assert report_file.exists()
        with open(report_file) as f:
            report_data = json.load(f)

        assert "timestamp" in report_data
        assert "result" in report_data
        assert "verbose" in report_data
        assert report_data["verbose"] is True
        assert report_data["result"]["status"] == "success"

    def test_generate_recommendations_no_issues(self, network_command):
        """Test recommendation generation when no issues are found."""
        result = NetworkDebugResult(status="success")
        result.dns_results["api.deepgram.com"] = DNSResult(
            hostname="api.deepgram.com",
            resolved=True,
            ip_addresses=["1.2.3.4"],
        )

        network_command._generate_recommendations(result)

        # No recommendations should be generated when everything is working
        assert (
            len(
                [
                    r
                    for r in result.recommendations
                    if r.startswith("❌") or r.startswith("⚠️")
                ]
            )
            == 0
        )

    def test_generate_recommendations_with_blocked_crl(self, network_command):
        """Test recommendation generation with blocked CRL endpoints."""
        result = NetworkDebugResult(status="success")

        tls_result = TLSTestResult(
            hostname="api.deepgram.com", port=443, connected=True
        )
        tls_result.revocation_endpoints = [
            RevocationEndpointTest(
                url="http://r11.o.lencr.org",
                endpoint_type="ocsp",
                accessible=False,
                error="Connection timeout",
            ),
            RevocationEndpointTest(
                url="http://r11.c.lencr.org/31.crl",
                endpoint_type="crl",
                accessible=False,
                error="Connection timeout",
            ),
        ]
        result.tls_test_results["api.deepgram.com"] = tls_result

        network_command._generate_recommendations(result)

        # Should have recommendations about blocked endpoints
        assert any(
            "Certificate revocation endpoints are blocked" in r
            for r in result.recommendations
        )
        assert any("r11.o.lencr.org" in r for r in result.recommendations)
        assert any("r11.c.lencr.org" in r for r in result.recommendations)

    @patch("socket.getaddrinfo")
    @patch("requests.get")
    @patch("subprocess.run")
    def test_handle_default_endpoint(
        self,
        mock_run,
        mock_get,
        mock_getaddrinfo,
        network_command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test handle method with default endpoint."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 0))
        ]

        # Mock HTTPS connectivity
        mock_get.return_value = Mock(status_code=404)

        # Mock subprocess calls
        mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

        result = network_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "success"
        assert "api.deepgram.com" in result.dns_results
        assert len(result.endpoint_results) > 0

    @patch("socket.getaddrinfo")
    @patch("requests.get")
    @patch("subprocess.run")
    def test_handle_custom_endpoint(
        self,
        mock_run,
        mock_get,
        mock_getaddrinfo,
        network_command,
        mock_config,
        mock_auth_manager,
        mock_client,
    ):
        """Test handle method with custom endpoint."""
        # Mock DNS resolution
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 0))
        ]

        # Mock HTTPS connectivity
        mock_get.return_value = Mock(status_code=200)

        # Mock subprocess calls
        mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

        result = network_command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            endpoint="auth.dx.deepgram.com",
        )

        assert result.status == "success"
        assert "auth.dx.deepgram.com" in result.dns_results
        assert result.endpoint_results[0].url == "https://auth.dx.deepgram.com"

    def test_collect_environment_info(self, network_command):
        """Test environment information collection."""
        result = NetworkDebugResult(status="success")
        network_command._collect_environment_info(result)

        assert "python_version" in result.environment_info
        assert "platform" in result.environment_info
        assert "requests_version" in result.environment_info
        assert "urllib3_version" in result.environment_info
        assert "proxy_env_vars" in result.environment_info

    def test_collect_environment_info_with_proxy(self, network_command):
        """Test environment information collection with proxy configured."""
        result = NetworkDebugResult(status="success")

        with patch.dict(
            "os.environ", {"https_proxy": "http://proxy.example.com:8080"}
        ):
            network_command._collect_environment_info(result)

        assert result.proxy_detected is True
        assert (
            result.proxy_settings["https_proxy"]
            == "http://proxy.example.com:8080"
        )
