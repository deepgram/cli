# Plugin Installation Guide

This guide covers how to install and manage plugins for deepctl in various scenarios.

## Overview

Deepctl supports external plugins that add custom commands. However, due to Python's environment isolation, plugin installation depends on how you installed deepctl.

## Installation Scenarios

### Scenario 1: Global Installation with pipx (Recommended)

This is the recommended approach for most users who want to use plugins.

```bash
# Install deepctl globally
pipx install deepctl

# Install plugins into the same environment
pipx inject deepctl deepctl-plugin-example
pipx inject deepctl your-custom-plugin

# Verify installation
deepctl --help  # Should show new plugin commands
```

**Managing plugins with pipx:**

```bash
# List all installed packages (including injected)
pipx list

# Upgrade deepctl and all plugins
pipx upgrade deepctl --include-injected

# Remove a plugin
pipx uninject deepctl plugin-name

# Reinstall everything fresh
pipx reinstall deepctl --include-injected
```

### Scenario 2: Development Environment

For development and testing, use a virtual environment:

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install deepctl and plugins together
pip install deepctl deepctl-plugin-example

# Or install from local development
pip install -e ./path/to/deepctl
pip install -e ./path/to/plugin
```

### Scenario 3: Using uv for Development

For faster development workflows:

```bash
# Create project and environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install deepctl and plugins
uv pip install deepctl deepctl-plugin-example

# Or from local paths
uv pip install -e ./deepctl -e ./my-plugin
```

### Scenario 4: Global Installation with uv tool (Limited Plugin Support)

If you've installed deepctl with `uv tool install`, plugin installation requires manual workarounds:

```bash
# Find where uv installed deepctl
uv tool dir

# Navigate to deepctl's environment
cd ~/.local/share/uv/tools/deepctl

# Manually install plugin
./bin/pip install deepctl-plugin-example
```

**Note:** This is not recommended for regular users. Use pipx instead for better plugin management.

## How Plugin Discovery Works

1. Plugins register themselves via Python entry points
2. When deepctl starts, it scans for packages with `deepctl.plugins` entry points
3. Plugins must be installed in the same Python environment as deepctl

This is why the installation method matters - deepctl can only find plugins installed in its own environment.

## Creating Your Own Plugin

See the [plugin example](../packages/deepctl-plugin-example) for a complete template. Key points:

1. Create a package that depends on `deepctl-core`
2. Register your command via entry points in `pyproject.toml`:
   ```toml
   [project.entry-points."deepctl.plugins"]
   mycommand = "my_plugin.command:MyCommand"
   ```
3. Publish to PyPI
4. Users install with: `pipx inject deepctl your-plugin-name`

## Troubleshooting

### Plugin not appearing after installation

1. **Check installation location**: Ensure the plugin is installed in the same environment as deepctl
2. **Verify entry points**: Run `pip show your-plugin` to confirm it's installed
3. **Check for errors**: Run deepctl with verbose logging to see plugin loading errors

### Multiple Python environments

If you have deepctl installed in multiple ways (pipx, system pip, venv), plugins must be installed in each environment separately.

### Permission errors

On some systems, you may need to use `pipx inject --force` if the plugin was previously installed incorrectly.

## Best Practices

1. **For end users**: Always use pipx for global installation with plugin support
2. **For developers**: Use virtual environments or uv for development
3. **For CI/CD**: Install deepctl and plugins together in the same pip command
4. **For distribution**: Publish plugins to PyPI with clear installation instructions

## Future Improvements

The deepctl team is exploring:

- Built-in plugin management commands
- Plugin marketplace/registry
- Automatic dependency resolution
- Better uv tool integration when inject-like functionality becomes available
