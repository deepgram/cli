# Workspace and Monorepo Architecture

## Overview

The Deepgram CLI repository is structured as a uv workspace (monorepo) to support multiple related packages while maintaining a unified development experience. This architecture allows us to:

1. Share common dependencies and tooling
2. Coordinate releases across packages
3. Enable cross-package development and testing
4. Maintain consistent code standards

## Workspace Layout

We use a **mixed layout** where:

- The root project (`deepctl`) serves as both the workspace root AND a workspace member
- Additional packages live in the `packages/` directory

```
cli/                              # Workspace root
├── pyproject.toml               # Workspace config + deepctl package
├── src/
│   └── deepgram_cli/           # Main CLI package source
├── packages/                    # Additional workspace packages
│   ├── package-a/
│   │   ├── pyproject.toml
│   │   └── src/
│   └── package-b/
│       ├── pyproject.toml
│       └── src/
└── ... (shared configs, docs, etc.)
```

## Workspace Configuration

The workspace is configured in the root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["packages/*"]
# Root is implicitly a member due to [project] section
```

## Package Types

### 1. Root Package (deepctl)

- The main Deepgram CLI application
- Provides core commands and functionality
- Serves as the entry point for users

### 2. Plugin Packages

Located in `packages/deepgram-plugin-*`:

- Extend CLI functionality
- Follow the plugin interface defined in `deepgram_cli.plugins`
- Can be optionally installed

### 3. Shared Libraries

Located in `packages/deepgram-shared-*`:

- Common utilities used across packages
- Reduce code duplication
- Maintain consistency

### 4. Tool Packages

Located in `packages/deepgram-tool-*`:

- Development tools
- Build utilities
- Testing helpers

## Development Workflow

### Installing Dependencies

From the workspace root:

```bash
# Install all workspace packages and dependencies
uv sync

# Install with specific extras
uv sync --extra dev --extra test
```

### Adding a New Package

1. Create the package structure:

```bash
mkdir -p packages/my-package/src/my_package
touch packages/my-package/pyproject.toml
touch packages/my-package/src/my_package/__init__.py
```

2. Configure the package `pyproject.toml`:

```toml
[project]
name = "deepgram-my-package"
version = "0.1.0"
description = "Description of my package"
dependencies = [
    # Package-specific dependencies
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

3. The package is automatically included in the workspace

### Cross-Package Dependencies

Packages can depend on each other:

```toml
# In packages/my-package/pyproject.toml
[project]
dependencies = [
    "deepctl",  # Depend on the root CLI package
    "deepgram-shared-utils",  # Depend on another workspace package
]
```

### Testing

Run tests for all packages:

```bash
# From workspace root
uv run pytest

# Test specific package
uv run pytest packages/my-package/tests/
```

### Building and Publishing

Build all packages:

```bash
# Build all workspace members
uv build

# Build specific package
cd packages/my-package && uv build
```

## Best Practices

### 1. Package Independence

- Each package should be independently useful
- Minimize circular dependencies
- Clear interfaces between packages

### 2. Shared Configuration

- Use workspace root for shared tool configs (pytest, mypy, etc.)
- Inherit common settings where possible
- Override only when necessary

### 3. Version Management

- Coordinate versions for related releases
- Use consistent versioning scheme
- Document breaking changes

### 4. Documentation

- Each package has its own README
- Cross-reference related packages
- Maintain workspace-level documentation

### 5. Testing Strategy

- Unit tests per package
- Integration tests at workspace level
- Cross-package compatibility tests

## Common Patterns

### Plugin Development

```python
# packages/deepgram-plugin-example/src/deepgram_plugin_example/plugin.py
from deepgram_cli.plugins import BasePlugin

class ExamplePlugin(BasePlugin):
    name = "example"
    description = "An example plugin"

    def register_commands(self, cli):
        # Add plugin commands
        pass
```

### Shared Utilities

```python
# packages/deepgram-shared-utils/src/deepgram_shared_utils/common.py
def shared_function():
    """Function used across multiple packages"""
    pass
```

## Migration Guide

When moving existing code to a package:

1. Identify the boundaries of the code to extract
2. Create the new package structure
3. Move the code maintaining import paths
4. Update imports in dependent code
5. Add appropriate dependencies
6. Test thoroughly

## Future Considerations

1. **Workspace Dependencies**: Consider using workspace dependencies for development
2. **Shared Build Tools**: Centralize build configuration
3. **Monorepo Tools**: Evaluate tools for change detection and selective testing
4. **Release Automation**: Implement coordinated release workflows
