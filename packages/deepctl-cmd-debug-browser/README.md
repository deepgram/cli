# deepctl-cmd-debug-browser

Browser debug subcommand for the Deepgram CLI (deepctl).

## Overview

This package provides the `browser` subcommand under the `debug` command group, allowing users to debug browser-related issues when using Deepgram's web-based features.

## Installation

This package is automatically installed as part of deepctl and should not be installed separately.

## Usage

```bash
# Debug a specific URL
deepctl debug browser --url https://example.com

# Alternative hyphenated syntax
deepctl debug-browser --url https://example.com

# Check browser connectivity with verbose output
deepctl debug browser --url https://api.deepgram.com --verbose

# Test with custom headers
deepctl debug browser --url https://example.com --header "Authorization: Bearer token"
```

## Features

- Test HTTP/HTTPS connectivity to URLs
- Check response times and status codes
- Verify SSL certificates
- Test with custom headers
- Display response headers and body (with --verbose flag)

## Development

This command extends `BaseCommand` and is registered as a subcommand of the `debug` group via the entry point `deepctl.subcommands.debug`.
