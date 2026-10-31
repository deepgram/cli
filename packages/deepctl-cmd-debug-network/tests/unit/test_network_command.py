"""Tests for the network debug command."""

import json
import socket
import ssl
import subprocess
from datetime import datetime
from unittest.mock import MagicMock, Mock, call, patch

import httpx
import pytest
from deepctl_cmd_debug_network import NetworkCommand
from deepctl_cmd_debug_network.models import (
    CertificateInfo,
    CommandExecutionResult,
    DNSResult,
    EndpointTestResult,
    NetworkDebugResult,
    PythonRequestsTest,
    RevocationEndpointTest,
    TLSTestResult,
)
from deepctl_core import AuthManager, Config, DeepgramClient


@pytest.fixture
def network_command():
    """Create network command instance."""
    return NetworkCommand()


class _MockHTTP:
    """Routes every request the command makes to an httpx.MockTransport and
    records the requests and the clients that carried them."""

    def __init__(self, command, monkeypatch):
        self.requests: list[httpx.Request] = []
        self.clients: list[dict] = []
        self.status_code = 404
        self.error: Exception | None = None
        command._transport = httpx.MockTransport(self._handle)
        original = command._http_client

        def recording_client(**kwargs):
            client = original(**kwargs)
            kwargs = {"verify": True, "follow_redirects": False, **kwargs}
            self.clients.append({"kwargs": kwargs, "client": client})
            return client

        monkeypatch.setattr(command, "_http_client", recording_client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status_code)


def _ssl_connect_error() -> httpx.ConnectError:
    """What httpx raises for a failed certificate check: a ConnectError
    whose cause is the ssl module's verification error."""
    try:
        try:
            raise ssl.SSLCertVerificationError(
                1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
            )
        except ssl.SSLError as exc:
            raise httpx.ConnectError(str(exc)) from exc
    except httpx.ConnectError as err:
        return err


@pytest.fixture
def mock_http(network_command, monkeypatch):
    """Every HTTP request goes to a MockTransport; nothing reaches the net."""
    return _MockHTTP(network_command, monkeypatch)


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
        endpoint_arg = next(arg for arg in args if "--endpoint" in arg["names"])
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
        skip_arg = next(arg for arg in args if "--skip-commands" in arg["names"])
        assert skip_arg["help"] == "Skip system command execution (openssl, curl, etc.)"
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
            simulate_arg["help"] == "Simulate blocked CRL/OCSP endpoints (for testing)"
        )
        assert simulate_arg["is_flag"] is True

        # Check save-report argument
        save_arg = next(arg for arg in args if "--save-report" in arg["names"])
        assert save_arg["help"] == "Save full diagnostic report to file"
        assert save_arg["type"] == str

        # Check test-websocket flag
        ws_arg = next(arg for arg in args if "--test-websocket" in arg["names"])
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
        mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")

        result = NetworkDebugResult(status="success")
        network_command._test_dns_resolution("api.deepgram.com", result, 10)

        assert "api.deepgram.com" in result.dns_results
        dns_result = result.dns_results["api.deepgram.com"]
        assert dns_result.resolved is False
        assert dns_result.error == "Name resolution failed"
        assert result.network_issues_detected is True

    def test_basic_connectivity_success(self, mock_http, network_command):
        """Test successful basic HTTPS connectivity."""
        mock_http.status_code = 404  # Expected for root endpoint

        result = NetworkDebugResult(status="success")
        network_command._test_basic_connectivity("api.deepgram.com", result, 10)

        assert len(result.endpoint_results) == 1
        endpoint = result.endpoint_results[0]
        assert endpoint.name == "HTTPS"
        assert endpoint.url == "https://api.deepgram.com"
        assert endpoint.reachable is True
        assert endpoint.status_code == 404
        assert endpoint.ssl_valid is True

    def test_basic_connectivity_ssl_error(self, mock_http, network_command):
        """Test basic connectivity with SSL error."""
        mock_http.error = _ssl_connect_error()

        result = NetworkDebugResult(status="success")
        network_command._test_basic_connectivity("api.deepgram.com", result, 10)

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

    def test_test_revocation_endpoints_success(self, mock_http, network_command):
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

        mock_http.status_code = 200
        network_command._test_revocation_endpoints(tls_result, result, False)

        assert len(tls_result.revocation_endpoints) == 3
        for endpoint in tls_result.revocation_endpoints:
            assert endpoint.accessible is True
            assert endpoint.status_code == 200
        assert {r.method for r in mock_http.requests} == {"HEAD"}
        assert all(c["kwargs"]["follow_redirects"] for c in mock_http.clients)

    def test_test_revocation_endpoints_simulated_block(
        self, mock_http, network_command
    ):
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

        mock_http.status_code = 200
        result = NetworkDebugResult(status="success")
        network_command._test_revocation_endpoints(
            tls_result, result, simulate_blocked_crl=True
        )

        # Check that CRL and OCSP endpoints are marked as blocked
        ocsp_endpoints = [
            e for e in tls_result.revocation_endpoints if e.endpoint_type == "ocsp"
        ]
        crl_endpoints = [
            e for e in tls_result.revocation_endpoints if e.endpoint_type == "crl"
        ]
        ca_endpoints = [
            e for e in tls_result.revocation_endpoints if e.endpoint_type == "ca_issuer"
        ]

        for endpoint in ocsp_endpoints + crl_endpoints:
            assert endpoint.accessible is False
            assert "simulated corporate firewall blocking" in endpoint.error

        for endpoint in ca_endpoints:
            assert endpoint.accessible is True  # CA issuer endpoints not blocked

    def test_python_requests_tests(self, mock_http, network_command):
        """The Python HTTP client tests run once with and once without
        certificate verification, each on its own client."""
        mock_http.status_code = 404

        result = NetworkDebugResult(status="success")
        network_command._test_python_requests("api.deepgram.com", result, 10)

        assert len(result.python_requests_tests) == 2

        # Test with SSL verification
        test_with_verify = result.python_requests_tests[0]
        assert test_with_verify.ssl_verify_enabled is True
        assert test_with_verify.success is True
        assert test_with_verify.status_code == 404
        assert test_with_verify.library == "httpx"

        # Test without SSL verification
        test_without_verify = result.python_requests_tests[1]
        assert test_without_verify.ssl_verify_enabled is False
        assert test_without_verify.success is True
        assert test_without_verify.status_code == 404
        assert test_without_verify.library == "httpx"

        # Two throwaway clients, the second alone with verify=False, both
        # closed after their one request.
        assert [c["kwargs"]["verify"] for c in mock_http.clients] == [True, False]
        assert len({id(c["client"]) for c in mock_http.clients}) == 2
        assert all(c["client"].is_closed for c in mock_http.clients)
        assert [str(r.url) for r in mock_http.requests] == [
            "https://api.deepgram.com/",
            "https://api.deepgram.com/",
        ]

    def test_python_requests_ssl_error(self, mock_http, network_command):
        """A failed certificate check is reported as an SSL failure."""
        mock_http.error = _ssl_connect_error()

        result = NetworkDebugResult(status="success")
        network_command._test_python_requests("api.deepgram.com", result, 10)

        with_verify, without_verify = result.python_requests_tests
        assert with_verify.success is False
        assert with_verify.error.startswith("SSL verification failed: ")
        assert "CERTIFICATE_VERIFY_FAILED" in with_verify.error
        assert without_verify.success is False
        assert result.network_issues_detected is True

    def test_python_requests_timeout(self, mock_http, network_command):
        """A timeout is a plain failure, not an SSL one."""
        mock_http.error = httpx.ConnectTimeout("timed out")

        result = NetworkDebugResult(status="success")
        network_command._test_python_requests("api.deepgram.com", result, 3)

        with_verify, without_verify = result.python_requests_tests
        assert with_verify.success is False
        assert with_verify.error == "timed out"
        assert without_verify.error == "timed out"
        assert result.network_issues_detected is False
        assert all(c["kwargs"]["timeout"] == 3 for c in mock_http.clients)

    def test_basic_connectivity_timeout(self, mock_http, network_command):
        """A timeout is not an SSL failure, but it is a network issue."""
        mock_http.error = httpx.ReadTimeout("read timed out")

        result = NetworkDebugResult(status="success")
        network_command._test_basic_connectivity("api.deepgram.com", result, 10)

        endpoint = result.endpoint_results[0]
        assert endpoint.reachable is False
        assert endpoint.ssl_valid is True  # the model default, as before
        assert endpoint.error == "read timed out"
        assert result.network_issues_detected is True

    def test_requests_carry_no_credentials(
        self, mock_http, network_command, monkeypatch
    ):
        """No diagnostic request sends an Authorization header, even with a
        key in the environment, and none follows a redirect except the
        revocation HEADs."""
        monkeypatch.setenv("DEEPGRAM_API_KEY", "sentinel-fake-key-w27")
        result = NetworkDebugResult(status="success")

        network_command._test_basic_connectivity("api.deepgram.com", result, 10)
        network_command._test_python_requests("api.deepgram.com", result, 10)

        assert len(mock_http.requests) == 3
        for request in mock_http.requests:
            assert "authorization" not in request.headers
            assert "sentinel-fake-key-w27" not in str(request.headers)
        assert not any(c["kwargs"]["follow_redirects"] for c in mock_http.clients)

    def test_http_client_trusts_the_environment(self, network_command):
        """The diagnostic client reads proxy/CA env vars like requests did."""
        with network_command._http_client(timeout=5) as client:
            assert client.trust_env is True
            assert client.follow_redirects is False
            assert client.timeout == httpx.Timeout(5)

    @patch("subprocess.run")
    def test_run_system_diagnostics(self, mock_run, network_command):
        """Test system diagnostic command execution."""
        with patch.object(network_command, "_command_exists", return_value=True):
            mock_run.return_value = Mock(
                returncode=0,
                stdout=b"PING api.deepgram.com: 3 packets transmitted, 3 received",
                stderr=b"",
            )

            result = NetworkDebugResult(status="success")
            network_command._run_system_diagnostics("api.deepgram.com", result, False)

            # Should have executed multiple commands
            assert len(result.command_results) > 0
            for cmd_result in result.command_results:
                if cmd_result.success:
                    assert cmd_result.exit_code == 0
                    assert cmd_result.stdout != ""

    @patch("subprocess.run")
    def test_test_websocket_connectivity(self, mock_run, network_command):
        """Test WebSocket connectivity testing."""
        with patch.object(network_command, "_command_exists", return_value=True):
            mock_run.return_value = Mock(
                returncode=0,
                stdout=b"HTTP/1.1 401 Unauthorized\r\n",
                stderr=b"",
            )

            result = NetworkDebugResult(status="success")
            network_command._test_websocket_connectivity("api.deepgram.com", result, 10)

            # Check WebSocket test was added to endpoint results
            ws_results = [e for e in result.endpoint_results if e.name == "WebSocket"]
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
    @patch("subprocess.run")
    def test_handle_default_endpoint(
        self,
        mock_run,
        mock_getaddrinfo,
        mock_http,
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
        mock_http.status_code = 404

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
    @patch("subprocess.run")
    def test_handle_custom_endpoint(
        self,
        mock_run,
        mock_getaddrinfo,
        mock_http,
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
        mock_http.status_code = 200

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
        assert "httpx_version" in result.environment_info
        assert result.environment_info["httpx_version"] == httpx.__version__
        assert "proxy_env_vars" in result.environment_info

    def test_collect_environment_info_with_proxy(self, network_command):
        """Test environment information collection with proxy configured."""
        result = NetworkDebugResult(status="success")

        with patch.dict("os.environ", {"https_proxy": "http://proxy.example.com:8080"}):
            network_command._collect_environment_info(result)

        assert result.proxy_detected is True
        assert result.proxy_settings["https_proxy"] == "http://proxy.example.com:8080"


# -- CA bundle environment, as requests resolved it ------------------------

_CA_VARS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


@pytest.fixture
def clean_ca_env(monkeypatch):
    for var in _CA_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"REQUESTS_CA_BUNDLE": "/r.pem"}, "/r.pem"),
        ({"CURL_CA_BUNDLE": "/c.pem"}, "/c.pem"),
        ({"REQUESTS_CA_BUNDLE": "/r.pem", "CURL_CA_BUNDLE": "/c.pem"}, "/r.pem"),
        ({"REQUESTS_CA_BUNDLE": "", "CURL_CA_BUNDLE": "/c.pem"}, "/c.pem"),
        ({"SSL_CERT_FILE": "/s.pem", "REQUESTS_CA_BUNDLE": "/r.pem"}, None),
        ({"SSL_CERT_DIR": "/s", "CURL_CA_BUNDLE": "/c.pem"}, None),
    ],
)
def test_requests_ca_bundle_precedence(clean_ca_env, env, expected):
    """SSL_CERT_FILE/SSL_CERT_DIR (httpx's own) win; then REQUESTS_CA_BUNDLE,
    then CURL_CA_BUNDLE."""
    from deepctl_cmd_debug_network.command import _requests_ca_bundle

    for var, value in env.items():
        clean_ca_env.setenv(var, value)
    assert _requests_ca_bundle() == expected


def test_verify_setting_uses_the_requests_bundle(clean_ca_env):
    import certifi
    from deepctl_cmd_debug_network.command import _verify_setting

    assert _verify_setting(True) is True
    clean_ca_env.setenv("REQUESTS_CA_BUNDLE", certifi.where())
    context = _verify_setting(True)
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] > 0
    # verify=False is never turned back on by the environment.
    assert _verify_setting(False) is False


def test_verify_setting_accepts_a_bundle_directory(clean_ca_env, tmp_path):
    from deepctl_cmd_debug_network.command import _verify_setting

    clean_ca_env.setenv("CURL_CA_BUNDLE", str(tmp_path))
    assert isinstance(_verify_setting(True), ssl.SSLContext)


def test_missing_bundle_is_reported_like_requests(
    clean_ca_env, mock_http, network_command, tmp_path
):
    """A bad REQUESTS_CA_BUNDLE path fails the verified request with
    requests' message; the verify=False request is unaffected."""
    clean_ca_env.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "missing.pem"))
    result = NetworkDebugResult(status="success")

    network_command._test_python_requests("api.deepgram.com", result, 10)

    with_verify, without_verify = result.python_requests_tests
    assert with_verify.success is False
    assert "Could not find a suitable TLS CA certificate bundle" in with_verify.error
    assert without_verify.success is True


def test_default_endpoint_comes_from_hosts_default():
    """The default host is hosts.DEFAULT_BASE_URL's, through the
    Transport; debug network sends no credentials anywhere."""
    from pathlib import Path

    from deepctl_cmd_debug_network import command as network_module
    from deepctl_core.hosts import DEFAULT_BASE_URL

    assert f"https://{network_module._default_host()}" == DEFAULT_BASE_URL
    source = Path(network_module.__file__).read_text(encoding="utf-8")
    for credential_use in ("get_api_key", "Authorization", "auth_headers"):
        assert credential_use not in source
