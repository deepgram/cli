# deepctl-cmd-debug-network

Network debug subcommand for the Deepgram CLI (deepctl).

## Overview

This package provides the `network` subcommand under the `debug` command group, allowing users to debug network connectivity issues when using Deepgram's API endpoints.

## Installation

This package is automatically installed as part of deepctl and should not be installed separately.

## Usage

```bash
# Test connectivity to Deepgram API
deepctl debug network

# Alternative hyphenated syntax
deepctl debug-network

# Test connectivity to specific Deepgram endpoint
deepctl debug network --endpoint transcription

# Test with custom timeout
deepctl debug network --timeout 10

# Verbose output with detailed diagnostics
deepctl debug network --verbose

# Test DNS resolution only
deepctl debug network --dns-only

# Test all Deepgram endpoints
deepctl debug network --all-endpoints
```

## Features

- Test connectivity to Deepgram API endpoints
- Check DNS resolution
- Measure latency to different regions
- Verify SSL/TLS connectivity
- Test WebSocket connectivity (for streaming)
- Check proxy settings if configured
- Display network route information (with --verbose)

## Development

This command extends `BaseCommand` and is registered as a subcommand of the `debug` group via the entry point `deepctl.subcommands.debug`.
