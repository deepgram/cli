# Plugin Command Compatibility Test

This document explains how the `deepctl plugin` command works with different installation methods and package sources.

## Compatibility Matrix

| Installation Method | Plugin Support  | Notes                                                         |
| ------------------- | --------------- | ------------------------------------------------------------- |
| **pip**             | ✅ Full Support | Direct installation into Python environment                   |
| **pipx**            | ✅ Full Support | Works within pipx's isolated environment                      |
| **uv**              | ✅ Full Support | Works within uv's isolated environment (requires pip)         |
| **brew (Homebrew)** | ✅ Full Support | Uses isolated plugin environment at `~/.deepctl/plugins/venv` |
| **chocolatey**      | ✅ Full Support | Uses isolated plugin environment at `~/.deepctl/plugins/venv` |
| **apt/yum/dnf**     | ✅ Full Support | Uses isolated plugin environment at `~/.deepctl/plugins/venv` |
| **Development**     | ✅ Full Support | Works in development environments                             |

## Installation Source Support

### 1. PyPI Packages

```bash
# Standard installation from PyPI
deepctl plugin install some-plugin

# Install specific version
deepctl plugin install some-plugin==1.0.0

# Install pre-release versions
deepctl plugin install some-plugin --pre
```

### 2. GitHub URLs

```bash
# Install from GitHub repository
deepctl plugin install git+https://github.com/user/repo.git

# Install specific branch
deepctl plugin install git+https://github.com/user/repo.git@branch-name

# Install specific tag/release
deepctl plugin install git+https://github.com/user/repo.git@v1.0.0

# Install specific commit
deepctl plugin install git+https://github.com/user/repo.git@commit-hash
```

### 3. Local Packages

```bash
# Install from local directory (editable)
deepctl plugin install -e /path/to/plugin

# Install from local wheel/tarball
deepctl plugin install /path/to/plugin.whl
deepctl plugin install /path/to/plugin.tar.gz
```

## How It Works

### Detection Logic

The plugin command detects the installation method by checking:

1. **pipx**: Looks for `.pipx` in the Python path
2. **uv**: Checks for `.uv` in the Python path
3. **System**: Checks if installed in system paths like `/usr/lib`, `/usr/local/lib`, `/opt`
4. **Development**: Checks for editable installations
5. **pip**: Default if in a virtual environment or user site-packages

### Installation Process

1. **Environment Detection**: The command first detects how deepctl was installed
2. **Environment Selection**:
   - For pip/pipx/uv installations: Uses the same environment as deepctl
   - For system installations (brew, apt, etc.): Creates an isolated plugin environment at `~/.deepctl/plugins/venv`
3. **Python Executable**: Uses the appropriate Python executable for the selected environment
4. **pip Integration**: Runs `python -m pip install` with appropriate flags

### Isolated Plugin Environment

For system installations (Homebrew, apt, yum, etc.), the plugin command automatically:

- Creates a virtual environment at `~/.deepctl/plugins/venv`
- Installs pip in this environment
- Tracks installed plugins in `~/.deepctl/plugins/plugins.json`
- Discovers plugins from both the main environment and the plugin environment

This approach allows ALL installation methods to support plugins while maintaining system package manager integrity.

**Example with Homebrew**:

```bash
# Install deepctl with Homebrew
brew install deepctl

# Plugins now work seamlessly!
deepctl plugin install some-plugin
# This creates ~/.deepctl/plugins/venv and installs the plugin there

# List plugins shows both built-in and external plugins
deepctl plugin list --verbose
```

### Installation Method Characteristics

Each installation method has its own approach:

**pip**:

- Direct installation into Python environment
- Plugins share the same environment as deepctl
- Simplest approach with no isolation

**pipx/uv**:

- Isolated virtual environments
- Plugins install within the same isolated environment
- Good balance of isolation and functionality

**System (brew/apt/yum)**:

- deepctl is read-only in system directories
- Plugins use isolated environment at `~/.deepctl/plugins/venv`
- Complete separation between system package and plugins

### pipx and uv Considerations

Both pipx and uv create isolated environments, but the plugin command handles this:

- Detects the isolated environment
- Installs plugins within that same environment
- Ensures plugins are discoverable by deepctl

**Note for uv users**: The first time you install a plugin, you may need to ensure pip is available:

```bash
uv pip install pip
```

## Examples

### Example 1: pip Installation

```bash
# Install deepctl with pip
pip install deepctl

# Install a plugin from PyPI
deepctl plugin install deepctl-plugin-example

# Install from GitHub
deepctl plugin install git+https://github.com/deepgram/deepctl-plugin-custom.git

# List plugins
deepctl plugin list
```

### Example 2: pipx Installation

```bash
# Install deepctl with pipx
pipx install deepctl

# Plugins work seamlessly
deepctl plugin install deepctl-plugin-example

# The plugin is installed in pipx's isolated environment
deepctl plugin list
```

### Example 3: Homebrew Installation (Full Plugin Support)

```bash
# Install deepctl with Homebrew
brew install deepctl

# Install a plugin - works seamlessly!
deepctl plugin install deepctl-plugin-example
# Creating plugin environment...
# Plugin environment created successfully
# Installing deepctl-plugin-example...
# Successfully installed deepctl-plugin-example

# List plugins with details
deepctl plugin list --verbose
# Shows plugins from both main environment (built-in) and plugin environment (external)

# The plugin is available immediately
deepctl example
```

## Best Practices

1. **Any installation method works**: All installation methods now support plugins
2. **Check installation details**: Run `deepctl plugin list --verbose` to see installation method and plugin environments
3. **GitHub URLs**: Always use the `git+` prefix for GitHub repositories
4. **Version pinning**: Specify versions for production environments
5. **Update regularly**: Use `deepctl plugin update <plugin>` to keep plugins current
6. **System installations**: Plugins are automatically managed in `~/.deepctl/plugins/`

## Troubleshooting

### "No module named pip"

This can happen with uv installations. Install pip first:

```bash
uv pip install pip
```

### Plugin not found after installation

Ensure the plugin follows the correct entry point specification:

- Must provide a `deepctl.commands` entry point
- Must inherit from `BaseCommand` or `BaseGroupCommand`
- Package name should follow the `deepctl-*` convention
