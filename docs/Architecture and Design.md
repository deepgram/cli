# Architecture and Design

## Overview

The Deepgram CLI (`deepctl`) is a Python-based command-line interface for Deepgram's speech recognition services. It emphasizes modularity, extensibility, and cross-platform compatibility.

## Core Principles

1. **Cross-platform Compatibility**: Works identically on Linux, Windows, macOS (Intel/Silicon)
2. **Modularity**: Each command is a separate package in the workspace
3. **Security**: Secure credential storage using system keyrings
4. **Extensibility**: Plugin system via Python entry points
5. **User Experience**: Simple installation via PyPI/uvx

## Technology Stack

- **Click**: CLI framework
- **Deepgram SDK**: Official Python SDK
- **Pydantic**: Data validation
- **Rich**: Terminal output formatting
- **httpx**: HTTP client
- **Keyring**: Secure credential storage
- **uv**: Package management

## Workspace Architecture

```
cli/
├── src/deepctl/           # Main CLI entry point
├── packages/              # Command packages
│   ├── deepctl-core/      # Shared core functionality
│   ├── deepctl-cmd-*/     # Individual commands
│   └── deepctl-shared-utils/
└── tests/                 # Main CLI tests
```

See [Workspace and Monorepo Architecture](Workspace%20and%20Monorepo%20Architecture.md) for detailed structure.

## Command System

Commands are discovered and loaded via Python entry points:

1. Each command package registers via `[project.entry-points."deepctl.commands"]`
2. Main CLI loads all registered commands at startup
3. Commands inherit from `BaseCommand` in deepctl-core

See [Modular Commands Architecture](Modular%20Commands%20Architecture.md) for implementation details.

## Authentication & Security

- OAuth2 device flow via community.deepgram.com
- Direct API key authentication
- Secure storage using system keyring (fallback to config file)
- Environment variable support for CI/CD

See [Authentication and Security Architecture](Authentication%20and%20Security%20Architecture.md) for details.

## Configuration

Hierarchical configuration system:

1. Command-line arguments (highest priority)
2. Environment variables
3. User config file (`~/.config/deepctl/config.yaml`)
4. Default values

## Distribution

- **PyPI**: Primary distribution (`uvx deepctl` or `pipx run deepctl`)
- **Aliases**: `deepgram` and `dg` available
- **Binary**: Future consideration via PyInstaller
