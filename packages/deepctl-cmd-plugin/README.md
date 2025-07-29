# deepctl-cmd-plugin

Plugin management command for deepctl CLI.

## Description

This command provides plugin management functionality for deepctl, allowing users to:

- Search for available plugins in the registry
- Install plugins from PyPI, GitHub, or local paths
- List installed plugins
- Update plugins to newer versions
- Remove plugins

This solves the environment isolation issue that occurs when deepctl is installed via pipx, uv, brew, or other package managers that create isolated environments.

## Commands

- `deepctl plugin search [query]` - Search for available plugins
- `deepctl plugin install <package>` - Install a plugin
- `deepctl plugin list` - List installed plugins
- `deepctl plugin update <package>` - Update a plugin
- `deepctl plugin remove <package>` - Remove a plugin

## Examples

### Search for plugins

```bash
# Show all available plugins
deepctl plugin search

# Search for plugins by keyword
deepctl plugin search audio

# Show only installed plugins
deepctl plugin search --installed

# Show only available (not installed) plugins
deepctl plugin search --available
```

### Install plugins

```bash
# Install from PyPI
deepctl plugin install deepctl-plugin-example

# Install from GitHub
deepctl plugin install git+https://github.com/deepgram/deepctl-plugin-custom.git

# Install specific version
deepctl plugin install deepctl-plugin-example==1.0.0

# Install from local directory
deepctl plugin install -e /path/to/plugin
```

## How it works

The plugin command automatically detects how deepctl was installed:

- **pip/pipx/uv**: Plugins install directly into the same environment
- **System installations (brew, apt, etc.)**: Plugins install into an isolated environment at `~/.deepctl/plugins/venv`

This approach ensures all installation methods support plugins while maintaining system package manager integrity.

## Plugin Registry

The `plugin search` command shows plugins from a curated registry. Currently, the registry is hardcoded but will eventually fetch from a `.well-known` URL or plugin registry service.

## Development

This is a built-in command that is part of the deepctl core distribution.
