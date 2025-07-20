# Workspace and Monorepo Architecture

## Overview

The deepctl repository is structured as a uv workspace (monorepo) to support multiple related packages while maintaining a unified development experience. This architecture allows us to:

- Share code between packages
- Ensure consistent versioning
- Simplify development workflow
- Enable easy plugin development

## Structure

```
deepgram-cli/                   # Repository root
├── pyproject.toml              # Workspace configuration
├── src/
│   └── deepctl/           # Main CLI package source
├── packages/                   # Workspace packages
│   ├── deepctl-core/          # Core functionality shared by all commands
│   ├── deepctl-cmd-login/     # Login command package
│   ├── deepctl-cmd-projects/  # Projects command package
│   ├── deepctl-cmd-transcribe/# Transcribe command package
│   ├── deepctl-cmd-usage/     # Usage command package
│   ├── deepctl-shared-utils/  # Shared utilities
│   └── deepctl-plugin-example/# Example plugin
├── tests/                      # Tests for main CLI
└── scripts/                    # Development scripts
```

## Packages

### Main Package: `deepctl`

Located in `src/deepctl/`, this is the main entry point for the CLI. It:

- Provides the command-line interface
- Loads commands from installed packages
- Handles global options and configuration

### Core Package: `deepctl-core`

The foundation package containing:

- Base command class
- Authentication system
- Configuration management
- Client initialization
- Output formatting
- Plugin system

### Command Packages

Each command is its own package:

- `deepctl-cmd-login` - Authentication commands
- `deepctl-cmd-projects` - Project management
- `deepctl-cmd-transcribe` - Transcription functionality
- `deepctl-cmd-usage` - Usage statistics

### Shared Utilities: `deepctl-shared-utils`

Common utilities used across packages:

- Input validation
- File handling
- Date/time utilities

### Plugin Example: `deepctl-plugin-example`

A reference implementation showing how to create plugins.

## Workspace Configuration

The root `pyproject.toml` defines the workspace:

```toml
[tool.uv.workspace]
members = ["packages/*"]

[project]
name = "deepctl"
dependencies = [
    "deepctl-core",
    "deepctl-cmd-login",
    "deepctl-cmd-projects",
    "deepctl-cmd-transcribe",
    "deepctl-cmd-usage",
    "deepctl-shared-utils",
]
```

## Development Workflow

### Installing for Development

```bash
# Install all packages in editable mode
uv sync

# Run the CLI
uv run deepctl --help
```

### Adding a New Command Package

1. Create package directory:

   ```bash
   mkdir -p packages/deepctl-cmd-newcmd/src/deepctl_cmd_newcmd
   ```

2. Add `pyproject.toml`:

   ```toml
   [project]
   name = "deepctl-cmd-newcmd"
   version = "0.1.0"
   dependencies = ["deepctl-core"]

   [project.entry-points."deepctl.commands"]
   newcmd = "deepctl_cmd_newcmd:NewCommand"
   ```

3. Implement the command following the base class interface

4. Update root `pyproject.toml` to include the new package

### Testing

Each package has its own test suite:

```bash
# Run all tests
uv run pytest

# Run tests for specific package
uv run pytest packages/deepctl-core/tests/

# Run with coverage
uv run pytest --cov
```

### Building and Distribution

```bash
# Build all packages
uv build

# Build specific package
cd packages/deepctl-core && uv build
```

## Plugin Development

External plugins can extend the CLI by:

1. Depending on `deepctl-core`
2. Implementing the `BaseCommand` interface
3. Registering via entry points:
   ```toml
   [project.entry-points."deepctl.commands"]
   myplugin = "my_plugin:MyCommand"
   ```

## Benefits of This Architecture

1. **Modularity**: Each command is independent
2. **Testability**: Packages can be tested in isolation
3. **Extensibility**: Easy to add new commands or plugins
4. **Maintainability**: Clear separation of concerns
5. **Distribution**: Can publish packages independently if needed

## Best Practices

1. Keep packages focused on single responsibilities
2. Use `deepctl-core` for shared functionality
3. Follow the established command interface
4. Include comprehensive tests with each package
5. Document public APIs

## Migration Notes

When migrating from a traditional structure:

1. Move command implementations to separate packages
2. Update imports to use the new package names
3. Ensure entry points are correctly registered
4. Test plugin discovery after migration
