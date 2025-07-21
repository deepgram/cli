# Workspace and Monorepo Architecture

## Overview

deepctl uses a uv workspace (monorepo) structure to manage multiple related packages while maintaining unified development.

## Package Structure

```
cli/                          # Repository root
├── pyproject.toml            # Workspace configuration
├── src/deepctl/              # Main CLI entry point
├── packages/                 # Workspace packages
│   ├── deepctl-core/         # Shared core functionality
│   ├── deepctl-cmd-*/        # Individual command packages
│   ├── deepctl-shared-utils/ # Common utilities
│   └── deepctl-plugin-example/
└── tests/                    # Main CLI tests
```

## Package Responsibilities

### deepctl (Main)

- CLI entry point and command discovery
- Global options handling
- Entry point registration

### deepctl-core

- `BaseCommand` class for all commands
- Authentication (`AuthManager`)
- Configuration (`Config`)
- Output formatting
- Deepgram client initialization

### Command Packages

Each command is isolated:

- `deepctl-cmd-login`: Authentication commands
- `deepctl-cmd-projects`: Project management
- `deepctl-cmd-transcribe`: Audio transcription
- `deepctl-cmd-usage`: Usage statistics

### deepctl-shared-utils

Utilities shared across commands:

- Input validation
- File handling helpers
- Common models

## Development Workflow

```bash
# Install all packages in editable mode
uv sync

# Run the CLI
uv run deepctl --help

# Test specific package
uv run pytest --package=deepctl-core

# Build all packages
uv build
```

## Adding New Commands

1. Create package: `packages/deepctl-cmd-newcmd/`
2. Add entry point in package's `pyproject.toml`
3. Add to root dependencies
4. Implement using `BaseCommand`

See [Modular Commands Architecture](Modular%20Commands%20Architecture.md) for details.

## Benefits

- **Independent versioning**: Packages can version separately
- **Clear boundaries**: Dependencies are explicit
- **Parallel development**: Teams can work on different commands
- **Easy testing**: Isolated test suites per package
