# Network Debug Command Architecture

## Overview

The `deepctl debug network` command is a comprehensive network diagnostic tool designed to help users troubleshoot connectivity issues with Deepgram services, particularly focusing on TLS/SSL certificate verification problems that commonly occur in corporate environments.

## Features

### Core Functionality

1. **DNS Resolution Testing**

   - Resolves hostnames to IP addresses
   - Measures resolution time
   - Detects DNS failures

2. **Basic HTTPS Connectivity**

   - Tests basic HTTP/HTTPS connectivity
   - Validates SSL certificates
   - Measures response times

3. **TLS/SSL Certificate Chain Analysis**

   - Extracts and analyzes the full certificate chain
   - Parses certificate details (subject, issuer)
   - Identifies OCSP, CRL, and CA issuer endpoints
   - Tests accessibility of revocation endpoints

4. **Python Requests Library Testing**

   - Tests with SSL verification enabled
   - Tests with SSL verification disabled (for comparison)
   - Helps identify SSL-specific issues

5. **System Command Diagnostics**

   - Runs ping, traceroute, openssl, and curl commands
   - Provides platform-specific command handling
   - Captures detailed output for analysis

6. **WebSocket Connectivity Testing** (optional)

   - Tests WebSocket upgrade capability
   - Validates WSS protocol support

7. **Environment Analysis**
   - Detects proxy configurations
   - Captures Python and library versions
   - Identifies potential environmental issues

### Command Options

```bash
deepctl debug network [OPTIONS]
```

Options:

- `--endpoint, -e`: Specific endpoint to test (default: api.deepgram.com)
- `--verbose, -v`: Show detailed diagnostic information
- `--skip-commands`: Skip system command execution (openssl, curl, etc.)
- `--timeout`: Timeout in seconds for network operations (default: 10)
- `--simulate-blocked-crl`: Simulate blocked CRL/OCSP endpoints (for testing)
- `--save-report`: Save full diagnostic report to file
- `--test-websocket`: Also test WebSocket connectivity (wss://)

## Architecture

### Models (`models.py`)

The command uses Pydantic models for type safety and data validation:

- `DNSResult`: DNS resolution results
- `EndpointTestResult`: Basic connectivity test results
- `CertificateInfo`: Certificate details from the chain
- `RevocationEndpointTest`: CRL/OCSP endpoint test results
- `TLSTestResult`: Complete TLS/SSL test results
- `PythonRequestsTest`: Python requests library test results
- `CommandExecutionResult`: System command execution results
- `NetworkDebugResult`: Aggregate result containing all test data

### Command Implementation (`command.py`)

The `NetworkCommand` class implements the following test phases:

1. **Environment Collection**: Gathers system and Python environment details
2. **DNS Resolution**: Tests hostname resolution
3. **Basic Connectivity**: Tests HTTPS connectivity
4. **TLS Certificate Analysis**: Extracts and analyzes the certificate chain
5. **Python Requests Tests**: Tests using the requests library
6. **System Diagnostics**: Runs system commands for additional diagnostics
7. **WebSocket Testing**: Optional WebSocket connectivity test

### Key Implementation Details

#### Certificate Revocation Testing

The command specifically tests accessibility of certificate revocation endpoints, which is crucial for identifying corporate firewall issues:

```python
def _test_revocation_endpoints(self, tls_result, result, simulate_blocked_crl=False):
    # Tests OCSP, CRL, and CA issuer endpoints
    # Can simulate blocked endpoints for testing
```

#### Smart Recommendations

The command analyzes results and provides actionable recommendations:

- Identifies blocked certificate revocation endpoints
- Suggests specific domains to whitelist
- Detects proxy configuration issues
- Provides context-specific troubleshooting steps

## Testing

The package includes comprehensive unit tests with 78% code coverage:

### Test Structure

- `test_models.py`: Tests for all Pydantic models
- `test_network_command.py`: Tests for the command implementation

### Key Test Scenarios

1. **DNS Resolution**: Success and failure cases
2. **SSL Connectivity**: Valid certificates and SSL errors
3. **Certificate Chain**: Parsing and analysis
4. **Revocation Endpoints**: Accessible and blocked scenarios
5. **Environment Detection**: Proxy configurations
6. **Report Generation**: File saving functionality

## Usage Examples

### Basic network diagnostic:

```bash
deepctl debug network
```

### Test specific endpoint with verbose output:

```bash
deepctl debug network --endpoint auth.dx.deepgram.com --verbose
```

### Simulate corporate firewall blocking CRL endpoints:

```bash
deepctl debug network --simulate-blocked-crl
```

### Save detailed report:

```bash
deepctl debug network --save-report network-diagnostics.json
```

## Integration

The command integrates seamlessly with the deepctl plugin system:

- Implements `BaseCommand` from `deepctl_core`
- Follows the plugin architecture patterns
- Provides structured output via Pydantic models
- Supports both human-readable and JSON output formats

## Security Considerations

- The command does not require authentication
- No sensitive data is transmitted
- Certificate validation is performed locally
- Proxy credentials (if any) are not logged or displayed
