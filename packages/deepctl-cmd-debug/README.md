# deepctl-cmd-debug

Debug command group for the Deepgram CLI (deepctl).

## Overview

This package provides the `debug` command group for deepctl, which contains various debugging utilities and diagnostic tools.

## Installation

This package is automatically installed as part of deepctl and should not be installed separately.

## Available Subcommands

- `deepctl debug browser` - Debug browser-related issues
- `deepctl debug network` - Debug network connectivity issues
- `deepctl debug audio` - Debug audio file issues

## Usage

```bash
# Show debug help
deepctl debug --help

# Run a specific debug subcommand
deepctl debug browser --url https://example.com

# Alternative hyphenated syntax also works
deepctl debug-browser --url https://example.com
```

## Development

This is a group command that doesn't perform any actions on its own. It serves as a container for debug-related subcommands.

To add new debug subcommands, create a new package following the naming pattern `deepctl-cmd-debug-{subcommand}` and register it with the entry point `deepctl.subcommands.debug`.
