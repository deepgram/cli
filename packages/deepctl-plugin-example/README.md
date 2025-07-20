# deepctl Plugin Example

An example plugin for deepctl demonstrating how to create and distribute custom commands as separate packages.

## Overview

This plugin shows how to extend deepctl with custom commands that can be:

- Developed independently
- Distributed via PyPI
- Installed alongside the CLI
- Discovered automatically at runtime

**Note:** This plugin is NOT a dependency of deepctl. It must be installed separately to demonstrate how the plugin system works for third-party developers.

## Installation

### Development Installation

To install the plugin in development mode:

```bash
# From the plugin directory
pip install -e .

# Or using uv
uv pip install -e .
```

### Production Installation

In a real-world scenario, you would publish this to PyPI and users would install it:

```bash
# Install the CLI
pip install deepctl

# Install the plugin separately
pip install deepctl-plugin-example
```

## Usage

Once installed, the plugin command becomes available in the CLI:

```bash
# Basic usage
deepctl example

# Custom greeting
deepctl example --greeting "Howdy" --name "Partner"

# Show plugin system information
deepctl example --show-info
```

## How It Works

1. **Entry Point Registration**: The plugin registers itself via the `deepctl.commands` entry point in `pyproject.toml`
2. **Command Discovery**: The CLI's PluginManager discovers the plugin at runtime using `importlib.metadata`
3. **Command Loading**: The plugin's command class is instantiated and added to the CLI
4. **Execution**: When the user runs the command, the plugin's `handle()` method is called

## Creating Your Own Plugin

To create your own deepctl plugin:

1. **Copy this example package** as a starting point
2. **Rename the package** and update metadata in `pyproject.toml`
3. **Implement your command** by modifying the `command.py` file
4. **Define arguments** in the `get_arguments()` method
5. **Implement logic** in the `handle()` method
6. **Test locally** with `pip install -e .`
7. **Publish to PyPI** when ready

### Key Requirements

- Must inherit from `deepctl_core.BaseCommand`
- Must implement required properties: `name`, `help`
- Must implement `handle()` method
- Must be registered via entry point

### Example Command Structure

```python
from deepctl_core import BaseCommand

class MyCommand(BaseCommand):
    name = "mycommand"
    help = "Description of my command"

    def handle(self, config, auth_manager, client, **kwargs):
        # Your command logic here
        pass
```

## Development

### Running Tests

```bash
uv run pytest
```

### Building the Package

```bash
python -m build
```

## Plugin Capabilities

Plugins have access to:

- Configuration management via `Config`
- Authentication via `AuthManager`
- Deepgram API client via `DeepgramClient`
- Rich terminal output via `rich`
- Command-line argument parsing via `click`

## License

MIT
