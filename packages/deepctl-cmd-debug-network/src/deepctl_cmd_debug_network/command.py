"""Network debug command for deepctl."""

import os
import re
import socket
import subprocess
import sys
import time
import warnings
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3
from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .models import (
    CertificateInfo,
    CommandExecutionResult,
    DNSResult,
    EndpointTestResult,
    NetworkDebugResult,
    PythonRequestsTest,
    RevocationEndpointTest,
    TLSTestResult,
)

console = Console()

# Default Deepgram endpoints to test
DEEPGRAM_ENDPOINTS = {
    "api": "https://api.deepgram.com",
    "auth": "https://auth.dx.deepgram.com",
}


class NetworkCommand(BaseCommand):
    """Debug network connectivity issues with Deepgram services."""

    name = "network"
    help = "Debug network connectivity issues with Deepgram services"
    short_help = "Debug network issues"

    # Network debug doesn't require auth
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> list[dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--endpoint", "-e"],
                "help": (
                    "Specific endpoint to test " "(default: api.deepgram.com)"
                ),
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--verbose", "-v"],
                "help": "Show detailed diagnostic information",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--skip-commands"],
                "help": "Skip system command execution (openssl, curl, etc.)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--timeout"],
                "help": (
                    "Timeout in seconds for network operations "
                    "(default: 10)"
                ),
                "type": int,
                "default": 10,
                "is_option": True,
            },
            {
                "names": ["--simulate-blocked-crl"],
                "help": "Simulate blocked CRL/OCSP endpoints (for testing)",
                "is_flag": True,
                "is_option": True,
            },
            {
                "names": ["--save-report"],
                "help": "Save full diagnostic report to file",
                "type": str,
                "is_option": True,
            },
            {
                "names": ["--test-websocket"],
                "help": "Also test WebSocket connectivity (wss://)",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle network debug command execution."""
        endpoint = kwargs.get("endpoint") or "api.deepgram.com"
        verbose = kwargs.get("verbose", False)
        skip_commands = kwargs.get("skip_commands", False)
        timeout = kwargs.get("timeout", 10)
        simulate_blocked_crl = kwargs.get("simulate_blocked_crl", False)
        save_report = kwargs.get("save_report")
        test_websocket = kwargs.get("test_websocket", False)

        # Initialize result
        result = NetworkDebugResult(status="success")

        # Show header
        console.print(
            Panel.fit(
                "[bold cyan]🔍 Deepgram Network Diagnostics[/bold cyan]\n\n"
                f"[dim]Testing connectivity to:[/dim] "
                f"[yellow]{endpoint}[/yellow]\n"
                "[dim]Running comprehensive TLS/SSL analysis...[/dim]",
                title="Network Debug",
                border_style="cyan",
            )
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:

            # 1. Collect environment information
            task = progress.add_task(
                "Collecting environment information...", total=None
            )
            self._collect_environment_info(result)
            progress.remove_task(task)

            # 2. DNS Resolution
            task = progress.add_task(
                "Performing DNS resolution...", total=None
            )
            self._test_dns_resolution(endpoint, result, timeout)
            progress.remove_task(task)

            # 3. Basic connectivity test
            task = progress.add_task(
                "Testing basic connectivity...", total=None
            )
            self._test_basic_connectivity(endpoint, result, timeout)
            progress.remove_task(task)

            # 4. TLS/SSL analysis
            if not skip_commands:
                task = progress.add_task(
                    "Analyzing TLS/SSL certificates...", total=None
                )
                self._analyze_tls_certificates(
                    endpoint, result, verbose, simulate_blocked_crl
                )
                progress.remove_task(task)

            # 5. Python requests tests
            task = progress.add_task(
                "Testing Python requests library...", total=None
            )
            self._test_python_requests(endpoint, result, timeout)
            progress.remove_task(task)

            # 6. System command tests
            if not skip_commands:
                task = progress.add_task(
                    "Running system diagnostics...", total=None
                )
                self._run_system_diagnostics(endpoint, result, verbose)
                progress.remove_task(task)

            # 7. WebSocket test (if requested)
            if test_websocket:
                task = progress.add_task(
                    "Testing WebSocket connectivity...", total=None
                )
                self._test_websocket_connectivity(endpoint, result, timeout)
                progress.remove_task(task)

        # Display results
        self._display_results(result, verbose)

        # Generate recommendations
        self._generate_recommendations(result)

        # Save report if requested
        if save_report:
            self._save_report(result, save_report, verbose)

        return result

    def _collect_environment_info(self, result: NetworkDebugResult) -> None:
        """Collect environment information."""
        result.environment_info = {
            "python_version": sys.version,
            "platform": sys.platform,
            "requests_version": requests.__version__,
            "urllib3_version": getattr(urllib3, "__version__", "unknown"),
            "proxy_env_vars": {
                "http_proxy": os.environ.get("http_proxy", "Not set"),
                "https_proxy": os.environ.get("https_proxy", "Not set"),
                "no_proxy": os.environ.get("no_proxy", "Not set"),
                "HTTP_PROXY": os.environ.get("HTTP_PROXY", "Not set"),
                "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "Not set"),
                "NO_PROXY": os.environ.get("NO_PROXY", "Not set"),
            },
        }

        # Check if proxy is configured
        if any(
            os.environ.get(var)
            for var in [
                "http_proxy",
                "https_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
            ]
        ):
            result.proxy_detected = True
            result.proxy_settings = result.environment_info["proxy_env_vars"]

    def _test_dns_resolution(
        self, hostname: str, result: NetworkDebugResult, timeout: int
    ) -> None:
        """Test DNS resolution for the hostname."""
        dns_result = DNSResult(hostname=hostname, resolved=False)

        try:
            start_time = time.time()
            ip_addresses = socket.getaddrinfo(hostname, None)
            dns_result.resolution_time_ms = (time.time() - start_time) * 1000

            # Extract unique IP addresses
            ips = list({str(addr[4][0]) for addr in ip_addresses})
            dns_result.ip_addresses = ips
            dns_result.resolved = True

        except Exception as e:
            dns_result.error = str(e)
            result.network_issues_detected = True

        result.dns_results[hostname] = dns_result

    def _test_basic_connectivity(
        self, hostname: str, result: NetworkDebugResult, timeout: int
    ) -> None:
        """Test basic HTTPS connectivity."""
        endpoint_test = EndpointTestResult(
            name="HTTPS", url=f"https://{hostname}", reachable=False
        )

        try:
            start_time = time.time()
            response = requests.get(
                f"https://{hostname}/", timeout=timeout, allow_redirects=False
            )
            endpoint_test.response_time_ms = (time.time() - start_time) * 1000
            endpoint_test.status_code = response.status_code
            endpoint_test.reachable = True
            endpoint_test.ssl_valid = True

        except requests.exceptions.SSLError as e:
            endpoint_test.error = f"SSL Error: {e!s}"
            endpoint_test.ssl_valid = False
            result.network_issues_detected = True
        except Exception as e:
            endpoint_test.error = str(e)
            result.network_issues_detected = True

        result.endpoint_results.append(endpoint_test)

    def _analyze_tls_certificates(
        self,
        hostname: str,
        result: NetworkDebugResult,
        verbose: bool,
        simulate_blocked_crl: bool = False,
    ) -> None:
        """Analyze TLS certificates and check revocation endpoints."""
        tls_result = TLSTestResult(
            hostname=hostname, port=443, connected=False
        )

        # Get certificate chain using openssl
        try:
            # First, get the full certificate chain
            cmd = [
                "openssl",
                "s_client",
                "-connect",
                f"{hostname}:443",
                "-showcerts",
            ]
            proc = subprocess.run(
                cmd, input=b"", capture_output=True, timeout=10
            )

            if verbose:
                tls_result.raw_openssl_output = proc.stdout.decode()

            if proc.returncode == 0:
                tls_result.connected = True

                # Parse certificates from output
                self._parse_certificate_chain(proc.stdout.decode(), tls_result)

                # Test revocation endpoints
                self._test_revocation_endpoints(
                    tls_result, result, simulate_blocked_crl
                )

            else:
                tls_result.chain_errors.append(
                    f"OpenSSL connection failed: {proc.stderr.decode()}"
                )
                result.network_issues_detected = True

        except subprocess.TimeoutExpired:
            tls_result.chain_errors.append("OpenSSL command timed out")
            result.network_issues_detected = True
        except Exception as e:
            tls_result.chain_errors.append(f"Error running OpenSSL: {e!s}")
            result.network_issues_detected = True

        result.tls_test_results[hostname] = tls_result

    def _parse_certificate_chain(
        self, openssl_output: str, tls_result: TLSTestResult
    ) -> None:
        """Parse certificate chain from OpenSSL output."""
        # Extract certificates
        cert_pattern = re.compile(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            re.DOTALL,
        )
        certificates = cert_pattern.findall(openssl_output)

        for i, cert_pem in enumerate(certificates):
            cert_info = CertificateInfo(subject="", issuer="")

            try:
                # Get certificate details
                proc = subprocess.run(
                    [
                        "openssl",
                        "x509",
                        "-noout",
                        "-subject",
                        "-issuer",
                        "-ext",
                        "crlDistributionPoints,authorityInfoAccess",
                    ],
                    input=cert_pem.encode(),
                    capture_output=True,
                    timeout=5,
                )

                if proc.returncode == 0:
                    output = proc.stdout.decode()

                    # Parse subject and issuer
                    subject_match = re.search(r"subject=(.+)", output)
                    issuer_match = re.search(r"issuer=(.+)", output)

                    if subject_match:
                        cert_info.subject = subject_match.group(1).strip()
                    if issuer_match:
                        cert_info.issuer = issuer_match.group(1).strip()

                    # Parse CRL distribution points
                    crl_matches = re.findall(r"URI:(.+)", output)
                    for url in crl_matches:
                        url = url.strip()
                        if url.startswith("http://") and "c.lencr.org" in url:
                            cert_info.crl_distribution_points.append(url)

                    # Parse OCSP and CA issuer URLs
                    ocsp_matches = re.findall(r"OCSP - URI:(.+)", output)
                    cert_info.ocsp_urls = [url.strip() for url in ocsp_matches]

                    ca_issuer_matches = re.findall(
                        r"CA Issuers - URI:(.+)", output
                    )
                    cert_info.ca_issuer_urls = [
                        url.strip() for url in ca_issuer_matches
                    ]

                    tls_result.certificate_chain.append(cert_info)

            except Exception as e:
                tls_result.chain_errors.append(
                    f"Error parsing certificate {i}: {e!s}"
                )

    def _test_revocation_endpoints(
        self,
        tls_result: TLSTestResult,
        result: NetworkDebugResult,
        simulate_blocked_crl: bool = False,
    ) -> None:
        """Test accessibility of CRL/OCSP/CA issuer endpoints."""
        all_endpoints = []

        for cert in tls_result.certificate_chain:
            # Add OCSP endpoints
            for url in cert.ocsp_urls:
                all_endpoints.append((url, "ocsp"))

            # Add CRL endpoints
            for url in cert.crl_distribution_points:
                all_endpoints.append((url, "crl"))

            # Add CA issuer endpoints
            for url in cert.ca_issuer_urls:
                all_endpoints.append((url, "ca_issuer"))

        # Test each unique endpoint
        tested_urls = set()
        for url, endpoint_type in all_endpoints:
            if url in tested_urls:
                continue
            tested_urls.add(url)

            endpoint_test = RevocationEndpointTest(
                url=url, endpoint_type=endpoint_type, accessible=False
            )

            # Simulate blocked CRL/OCSP endpoints for testing
            if simulate_blocked_crl and endpoint_type in ["crl", "ocsp"]:
                endpoint_test.error = (
                    "Connection timed out (simulated corporate "
                    "firewall blocking)"
                )
                endpoint_test.accessible = False
                result.network_issues_detected = True
            else:
                try:
                    start_time = time.time()
                    response = requests.head(
                        url, timeout=5, allow_redirects=True
                    )
                    endpoint_test.response_time_ms = (
                        time.time() - start_time
                    ) * 1000
                    endpoint_test.status_code = response.status_code
                    endpoint_test.accessible = response.status_code < 400

                except Exception as e:
                    endpoint_test.error = str(e)
                    result.network_issues_detected = True

            tls_result.revocation_endpoints.append(endpoint_test)

    def _test_python_requests(
        self, hostname: str, result: NetworkDebugResult, timeout: int
    ) -> None:
        """Test connectivity using Python requests library."""
        # Test with SSL verification enabled
        test_with_verify = PythonRequestsTest(
            url=f"https://{hostname}/", success=False, ssl_verify_enabled=True
        )

        try:
            start_time = time.time()
            response = requests.get(
                f"https://{hostname}/",
                timeout=timeout,
                verify=True,
                allow_redirects=False,
            )
            test_with_verify.response_time_ms = (
                time.time() - start_time
            ) * 1000
            test_with_verify.status_code = response.status_code
            test_with_verify.success = True

        except requests.exceptions.SSLError as e:
            test_with_verify.error = f"SSL verification failed: {e!s}"
            result.network_issues_detected = True
        except Exception as e:
            test_with_verify.error = str(e)

        result.python_requests_tests.append(test_with_verify)

        # Test with SSL verification disabled (for comparison)
        test_without_verify = PythonRequestsTest(
            url=f"https://{hostname}/", success=False, ssl_verify_enabled=False
        )

        try:
            # Disable SSL warnings temporarily
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            start_time = time.time()
            response = requests.get(
                f"https://{hostname}/",
                timeout=timeout,
                verify=False,
                allow_redirects=False,
            )
            test_without_verify.response_time_ms = (
                time.time() - start_time
            ) * 1000
            test_without_verify.status_code = response.status_code
            test_without_verify.success = True

        except Exception as e:
            test_without_verify.error = str(e)
        finally:
            # Re-enable warnings
            warnings.resetwarnings()

        result.python_requests_tests.append(test_without_verify)

    def _run_system_diagnostics(
        self, hostname: str, result: NetworkDebugResult, verbose: bool
    ) -> None:
        """Run system diagnostic commands."""
        commands = [
            # Basic connectivity
            ["ping", "-c", "3", hostname],
            # Traceroute (platform-specific)
            (
                ["traceroute", "-m", "10", hostname]
                if sys.platform != "win32"
                else ["tracert", "-h", "10", hostname]
            ),
            # OpenSSL connection test
            [
                "openssl",
                "s_client",
                "-connect",
                f"{hostname}:443",
                "-servername",
                hostname,
            ],
            # Curl test
            ["curl", "-I", "--connect-timeout", "10", f"https://{hostname}/"],
            # Curl with verbose SSL info
            ["curl", "-v", "--connect-timeout", "10", f"https://{hostname}/"],
        ]

        for cmd in commands:
            # Skip commands that don't exist on the system
            if not self._command_exists(cmd[0]):
                continue

            cmd_result = CommandExecutionResult(
                command=" ".join(cmd), success=False, exit_code=-1
            )

            try:
                start_time = time.time()

                # Special handling for openssl s_client (needs input)
                if cmd[0] == "openssl" and "s_client" in cmd:
                    proc = subprocess.run(
                        cmd, input=b"", capture_output=True, timeout=15
                    )
                else:
                    proc = subprocess.run(cmd, capture_output=True, timeout=15)

                cmd_result.execution_time_ms = (
                    time.time() - start_time
                ) * 1000
                cmd_result.exit_code = proc.returncode
                cmd_result.success = proc.returncode == 0

                # Limit output size for non-verbose mode
                if verbose or cmd_result.success:
                    cmd_result.stdout = proc.stdout.decode()[:5000]
                    cmd_result.stderr = proc.stderr.decode()[:5000]
                else:
                    cmd_result.stdout = proc.stdout.decode()[:1000]
                    cmd_result.stderr = proc.stderr.decode()[:1000]

            except subprocess.TimeoutExpired:
                cmd_result.stderr = "Command timed out"
            except Exception as e:
                cmd_result.stderr = str(e)

            result.command_results.append(cmd_result)

    def _command_exists(self, command: str) -> bool:
        """Check if a command exists on the system."""
        try:
            subprocess.run(
                (
                    ["which", command]
                    if sys.platform != "win32"
                    else ["where", command]
                ),
                capture_output=True,
                check=False,
            )
            return True
        except Exception:
            return False

    def _test_websocket_connectivity(
        self, hostname: str, result: NetworkDebugResult, timeout: int
    ) -> None:
        """Test WebSocket connectivity to the endpoint."""
        ws_test = EndpointTestResult(
            name="WebSocket",
            url=f"wss://{hostname}/v1/listen",
            reachable=False,
        )

        # Test with curl if available
        if self._command_exists("curl"):
            cmd_result = CommandExecutionResult(
                command=(
                    f"curl -i -N -H 'Connection: Upgrade' "
                    f"-H 'Upgrade: websocket' "
                    f"-H 'Sec-WebSocket-Key: test' "
                    f"-H 'Sec-WebSocket-Version: 13' "
                    f"https://{hostname}/v1/listen"
                ),
                success=False,
                exit_code=-1,
            )

            try:
                start_time = time.time()
                proc = subprocess.run(
                    [
                        "curl",
                        "-i",
                        "-N",
                        "-H",
                        "Connection: Upgrade",
                        "-H",
                        "Upgrade: websocket",
                        "-H",
                        "Sec-WebSocket-Key: test",
                        "-H",
                        "Sec-WebSocket-Version: 13",
                        f"https://{hostname}/v1/listen",
                    ],
                    capture_output=True,
                    timeout=timeout,
                )

                cmd_result.execution_time_ms = (
                    time.time() - start_time
                ) * 1000
                cmd_result.exit_code = proc.returncode
                cmd_result.success = proc.returncode == 0
                cmd_result.stdout = proc.stdout.decode()[:1000]
                cmd_result.stderr = proc.stderr.decode()[:1000]

                # Check if we got a valid WebSocket upgrade response
                if "HTTP" in cmd_result.stdout and (
                    "101" in cmd_result.stdout or "401" in cmd_result.stdout
                ):
                    ws_test.reachable = True
                    ws_test.status_code = (
                        101 if "101" in cmd_result.stdout else 401
                    )

            except Exception as e:
                cmd_result.stderr = str(e)

            result.command_results.append(cmd_result)

        result.endpoint_results.append(ws_test)

    def _save_report(
        self, result: NetworkDebugResult, filename: str, verbose: bool
    ) -> None:
        """Save diagnostic report to file."""
        import json
        from datetime import datetime

        try:
            # Prepare report data
            report = {
                "timestamp": datetime.now().isoformat(),
                "result": result.model_dump(),
                "verbose": verbose,
            }

            # Save to file
            with open(filename, "w") as f:
                json.dump(report, f, indent=2)

            console.print(f"\n[green]✅ Report saved to: {filename}[/green]")

        except Exception as e:
            console.print(f"\n[red]❌ Failed to save report: {e!s}[/red]")

    def _display_results(
        self, result: NetworkDebugResult, verbose: bool
    ) -> None:
        """Display diagnostic results."""
        console.print("\n[bold cyan]📊 Diagnostic Results[/bold cyan]\n")

        # DNS Results
        if result.dns_results:
            console.print("[bold]DNS Resolution:[/bold]")
            for hostname, dns_result in result.dns_results.items():
                if dns_result.resolved:
                    console.print(
                        f"  ✅ {hostname} → "
                        f"{', '.join(dns_result.ip_addresses)}"
                    )
                    if dns_result.resolution_time_ms:
                        console.print(
                            f"     [dim]Resolution time: "
                            f"{dns_result.resolution_time_ms:.2f}ms[/dim]"
                        )
                else:
                    console.print(f"  ❌ {hostname} - {dns_result.error}")
            console.print()

        # Basic Connectivity
        if result.endpoint_results:
            console.print("[bold]Basic Connectivity:[/bold]")
            for endpoint in result.endpoint_results:
                if endpoint.reachable:
                    console.print(f"  ✅ {endpoint.name} - {endpoint.url}")
                    if endpoint.response_time_ms:
                        console.print(
                            f"     [dim]Response time: "
                            f"{endpoint.response_time_ms:.2f}ms[/dim]"
                        )
                else:
                    console.print(f"  ❌ {endpoint.name} - {endpoint.error}")
            console.print()

        # TLS/SSL Analysis
        if result.tls_test_results:
            console.print("[bold]TLS/SSL Certificate Chain:[/bold]")
            for hostname, tls_result in result.tls_test_results.items():
                if tls_result.connected:
                    console.print(f"  ✅ Connected to {hostname}:443")

                    # Show certificate chain
                    for i, cert in enumerate(tls_result.certificate_chain):
                        console.print(
                            f"\n  [bold]Certificate #{i + 1}:[/bold]"
                        )
                        console.print(
                            f"    Subject: [yellow]{cert.subject}[/yellow]"
                        )
                        console.print(
                            f"    Issuer: [cyan]{cert.issuer}[/cyan]"
                        )

                        if verbose:
                            if cert.ocsp_urls:
                                console.print(
                                    f"    OCSP URLs: "
                                    f"{', '.join(cert.ocsp_urls)}"
                                )
                            if cert.ca_issuer_urls:
                                console.print(
                                    f"    CA Issuer URLs: "
                                    f"{', '.join(cert.ca_issuer_urls)}"
                                )
                            if cert.crl_distribution_points:
                                crl_urls = ", ".join(
                                    cert.crl_distribution_points
                                )
                                console.print(f"    CRL URLs: {crl_urls}")

                    # Show revocation endpoint test results
                    if tls_result.revocation_endpoints:
                        console.print(
                            "\n  [bold]Revocation Endpoint Tests:[/bold]"
                        )

                        # Group by type
                        by_type: dict[str, list[Any]] = {}
                        for rev_endpoint in tls_result.revocation_endpoints:
                            if rev_endpoint.endpoint_type not in by_type:
                                by_type[rev_endpoint.endpoint_type] = []
                            by_type[rev_endpoint.endpoint_type].append(
                                rev_endpoint
                            )

                        for endpoint_type, endpoints in by_type.items():
                            console.print(
                                f"\n    [yellow]{endpoint_type.upper()} "
                                f"Endpoints:[/yellow]"
                            )
                            for endpoint in endpoints:
                                if endpoint.accessible:
                                    console.print(f"      ✅ {endpoint.url}")
                                    if verbose and endpoint.response_time_ms:
                                        resp_time = (
                                            f"{endpoint.response_time_ms:.2f}"
                                        )
                                        console.print(
                                            f"         [dim]Response time: "
                                            f"{resp_time}ms[/dim]"
                                        )
                                else:
                                    console.print(f"      ❌ {endpoint.url}")
                                    if endpoint.error:
                                        console.print(
                                            f"         [red]Error: "
                                            f"{endpoint.error}[/red]"
                                        )

                else:
                    console.print(f"  ❌ Failed to connect to {hostname}:443")
                    for error in tls_result.chain_errors:
                        console.print(f"     [red]{error}[/red]")
            console.print()

        # Python Requests Tests
        if result.python_requests_tests:
            console.print("[bold]Python Requests Library Tests:[/bold]")
            for test in result.python_requests_tests:
                verify_status = (
                    "with SSL verification"
                    if test.ssl_verify_enabled
                    else "without SSL verification"
                )
                if test.success:
                    console.print(
                        f"  ✅ Successfully connected {verify_status}"
                    )
                    if test.response_time_ms:
                        console.print(
                            f"     [dim]Response time: "
                            f"{test.response_time_ms:.2f}ms[/dim]"
                        )
                else:
                    console.print(f"  ❌ Failed to connect {verify_status}")
                    if test.error:
                        console.print(f"     [red]{test.error}[/red]")
            console.print()

        # Proxy Detection Warning
        if result.proxy_settings:
            console.print(
                "[bold yellow]⚠️  Proxy Configuration Detected:[/bold yellow]"
            )
            for var, value in result.proxy_settings.items():
                if value and value != "Not set":
                    console.print(f"  {var}: {value}")
            console.print()

        # Command Results (in verbose mode)
        if verbose and result.command_results:
            console.print("[bold]System Command Results:[/bold]")
            for cmd_result in result.command_results:
                status = "✅" if cmd_result.success else "❌"
                console.print(
                    f"\n  {status} Command: [cyan]{cmd_result.command}[/cyan]"
                )
                console.print(f"     Exit code: {cmd_result.exit_code}")
                if cmd_result.stdout.strip():
                    console.print("     [dim]Output:[/dim]")
                    for line in cmd_result.stdout.strip().split("\n")[:10]:
                        console.print(f"       {line}")
                if cmd_result.stderr.strip():
                    console.print("     [dim red]Errors:[/dim red]")
                    for line in cmd_result.stderr.strip().split("\n")[:5]:
                        console.print(f"       {line}")

    def _generate_recommendations(self, result: NetworkDebugResult) -> None:
        """Generate recommendations based on diagnostic results."""
        recommendations = []

        # Check DNS issues
        for hostname, dns_result in result.dns_results.items():
            if not dns_result.resolved:
                recommendations.append(
                    f"❌ DNS resolution failed for {hostname}. "
                    f"Check your DNS settings and network connectivity."
                )

        # Check TLS/SSL issues
        for hostname, tls_result in result.tls_test_results.items():
            if not tls_result.connected:
                recommendations.append(
                    f"❌ Failed to establish TLS connection to {hostname}. "
                    f"This may indicate a firewall or network issue."
                )

            # Check revocation endpoint accessibility
            blocked_endpoints = []
            for endpoint in tls_result.revocation_endpoints:
                if not endpoint.accessible:
                    blocked_endpoints.append(endpoint)

            if blocked_endpoints:
                recommendations.append(
                    "⚠️  Certificate revocation endpoints are blocked. "
                    "This is a security issue that prevents proper "
                    "certificate validation."
                )
                recommendations.append(
                    "   To fix this, ensure the following domains are "
                    "accessible on port 80 (HTTP):"
                )

                # Group by domain
                domains = set()
                for endpoint in blocked_endpoints:
                    parsed = urlparse(endpoint.url)
                    domains.add(parsed.netloc)

                for domain in sorted(domains):
                    recommendations.append(f"   • {domain}")

                recommendations.append(
                    "   These endpoints are required by Let's Encrypt for "
                    "certificate validation."
                )

        # Check Python SSL issues
        ssl_verify_failed = False
        ssl_noverify_success = False

        for test in result.python_requests_tests:
            if (
                test.ssl_verify_enabled
                and not test.success
                and "SSL" in str(test.error)
            ):
                ssl_verify_failed = True
            elif not test.ssl_verify_enabled and test.success:
                ssl_noverify_success = True

        if ssl_verify_failed and ssl_noverify_success:
            recommendations.append(
                "⚠️  Python requests fails with SSL verification but "
                "succeeds without it."
            )
            recommendations.append(
                "   This typically indicates certificate validation "
                "issues, possibly due to:"
            )
            recommendations.append(
                "   • Blocked certificate revocation endpoints (see above)"
            )
            recommendations.append("   • Missing or outdated CA certificates")
            recommendations.append("   • Corporate proxy interference")

        # Check proxy configuration
        if result.proxy_detected:
            recommendations.append(
                "ℹ️  Proxy configuration detected. Ensure your proxy "
                "allows access to:"
            )
            recommendations.append("   • api.deepgram.com (port 443)")
            recommendations.append(
                "   • Certificate validation endpoints (port 80)"
            )

        # Display recommendations
        if recommendations:
            console.print("\n[bold yellow]📋 Recommendations:[/bold yellow]\n")
            for rec in recommendations:
                console.print(f"  {rec}")
        else:
            console.print(
                "\n[bold green]✅ All network diagnostics passed![/bold green]"
            )

        result.recommendations = recommendations
