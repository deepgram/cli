# deepctl Documentation

Welcome to the deepctl documentation. This wiki contains architectural decisions and development guides for the Deepgram CLI.

## Architecture Documentation

- **[Architecture and Design](Architecture%20and%20Design.md)** - High-level overview of the CLI architecture, technology stack, and design principles

- **[Workspace and Monorepo Architecture](Workspace%20and%20Monorepo%20Architecture.md)** - Details about the uv workspace structure and package organization

- **[Modular Commands Architecture](Modular%20Commands%20Architecture.md)** - How commands are structured as independent packages and loaded via entry points

- **[Commands and Plugins Architecture](Commands%20and%20Plugins%20Architecture.md)** - Distinction between built-in commands and external plugins, with entry point groups

- **[MCP Server Command Architecture](MCP%20Server%20Command%20Architecture.md)** - Implementation of the Model Context Protocol server for Deepgram's Gnosis AI service

## Security & Authentication

- **[Authentication and Security Architecture](Authentication%20and%20Security%20Architecture.md)** - Authentication methods, credential storage, and security features

- **[API Key Verification Architecture](API%20Key%20Verification%20Architecture.md)** - How credentials are verified before command execution

## Development Guides

- **[Development Guide With Uv](Development%20Guide%20With%20Uv.md)** - Complete guide for developing with the uv package manager

- **[Cross-Platform Development Guide](Cross-Platform%20Development%20Guide.md)** - Best practices for maintaining cross-platform compatibility

## Testing

- **[Testing and Test Strategy](Testing%20and%20Test%20Strategy.md)** - Overview of the testing approach and requirements

- **[Testing With Flexible Options](Testing%20With%20Flexible%20Options.md)** - How to use the custom test runner for monorepo testing

## Quick Links

- Main repository: `/cli/`
- Core package: `/cli/packages/deepctl-core/`
- Command packages: `/cli/packages/deepctl-cmd-*/`
- Test runner: `/cli/scripts/test_runner.py`

## Key Commands

```bash
# Install development environment
uv sync

# Run CLI
uv run deepctl --help

# Run tests
uv run pytest              # Main CLI tests only
uv run pytest --all        # All tests
uv run pytest --package=deepctl-core  # Specific package

# Build packages
uv build
```
