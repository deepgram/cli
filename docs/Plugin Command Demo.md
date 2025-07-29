# Plugin Command Demo

This demo shows how the `deepctl plugin` command now works with ALL installation methods, including Homebrew!

## Key Innovation

Based on insights from GPT-3, we've implemented an isolated plugin environment approach that allows system installations (Homebrew, apt, etc.) to support plugins by:

1. Creating a virtual environment at `~/.deepctl/plugins/venv`
2. Installing plugins into this isolated environment
3. Tracking plugin state in `~/.deepctl/plugins/plugins.json`
4. Discovering plugins from both the main environment and plugin environment

## Demo: Homebrew Installation

```bash
# Install deepctl with Homebrew
brew install deepctl

# Check plugin support - it works!
deepctl plugin list
# Output: No plugins installed

# Install a plugin from PyPI
deepctl plugin install deepctl-plugin-example
# Output:
# ℹ System installation detected, using isolated plugin environment...
# ℹ Creating plugin environment...
# ✅ Plugin environment created successfully
# ℹ Installing deepctl-plugin-example...
# ✅ Successfully installed deepctl-plugin-example

# List plugins - shows it's in the plugin environment
deepctl plugin list --verbose
# Shows table with plugin in "Plugin Env"

# The plugin command is now available
deepctl example
# Output: Hello from the example plugin!

# Install from GitHub
deepctl plugin install git+https://github.com/deepgram/deepctl-plugin-custom.git
# Works seamlessly!
```

## Demo: pip Installation

```bash
# Install with pip
pip install deepctl

# Install a plugin - goes into the same environment
deepctl plugin install deepctl-plugin-example
# ℹ Installing deepctl-plugin-example...
# ✅ Successfully installed deepctl-plugin-example

# List plugins
deepctl plugin list --verbose
# Shows plugin in "Main Env"
```

## Demo: pipx Installation

```bash
# Install with pipx
pipx install deepctl

# Install a plugin - goes into pipx's isolated environment
deepctl plugin install deepctl-plugin-example
# Works within pipx's environment

# No need for pipx inject anymore!
```

## How It Works

### Detection Logic

The plugin command detects the installation method:

```python
# For system installations (Homebrew, apt, etc.)
if install_info.method in [InstallMethod.SYSTEM, InstallMethod.UNKNOWN]:
    # Use isolated plugin environment
    success, python_exe = self._ensure_plugin_environment()
    target_python = python_exe
    using_plugin_env = True
else:
    # For pip/pipx/uv, use the same environment
    target_python = self._python_executable
    using_plugin_env = False
```

### Plugin Environment Structure

```
~/.deepctl/
├── plugins/
│   ├── venv/              # Isolated virtual environment
│   │   ├── bin/
│   │   │   └── python     # Python executable for plugins
│   │   └── lib/
│   │       └── python3.x/
│   │           └── site-packages/  # Installed plugins
│   └── plugins.json       # Plugin state tracking
```

### Plugin Discovery

The plugin manager discovers from both environments:

```python
def _discover_plugins(self) -> list[PluginPackage]:
    plugins = []

    # Discover from main environment
    plugins.extend(self._discover_from_environment(sys.executable))

    # Also discover from plugin environment if it exists
    if self._plugin_venv.exists():
        _, plugin_python = self._ensure_plugin_environment()
        plugin_env_plugins = self._discover_from_environment(plugin_python)
        plugins.extend(plugin_env_plugins)

    return plugins
```

## Benefits

1. **Universal Plugin Support**: All installation methods now support plugins
2. **System Integrity**: System package managers remain unaffected
3. **Seamless Experience**: Users don't need to know about the underlying complexity
4. **Future-Proof**: Works with any package manager that creates system installations

## Testing

To test this yourself:

```bash
# Simulate a system installation by installing in a system-like path
sudo pip install --prefix /usr/local deepctl

# Now test plugin installation
deepctl plugin install deepctl-plugin-example
# It will automatically use the isolated plugin environment!
```

## Conclusion

This approach solves the fundamental incompatibility between system package managers and Python plugin systems. Users can now enjoy the convenience of Homebrew, apt, or any other system package manager while still having full plugin support.

The key insight from GPT-3 was that we don't need to fight the system package manager - we can work alongside it by creating our own isolated environment specifically for plugins.
