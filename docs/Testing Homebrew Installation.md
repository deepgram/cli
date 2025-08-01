# Testing Homebrew Installation

This guide explains how to test the plugin system with a simulated Homebrew installation without actually releasing to Homebrew.

## Quick Test

Run the automated test script:

```bash
./scripts/test_homebrew_simulation.sh
```

## Manual Testing Steps

### 1. Build the Package

First, build the deepctl wheel:

```bash
make build
# or manually:
# uv build
```

### 2. Create a Test Environment

Create a virtual environment that simulates Homebrew's Python:

```bash
# Create test directory
mkdir -p /tmp/homebrew-test
cd /tmp/homebrew-test

# Create virtual environment
python3 -m venv brew-python

# Activate it
source brew-python/bin/activate

# Install deepctl
pip install /path/to/deepctl/dist/deepctl-*.whl

# Deactivate
deactivate
```

### 3. Simulate System Installation

Make the installation read-only to trigger system installation detection:

```bash
# Make the virtual environment read-only
chmod -R a-w brew-python/

# Now deepctl will detect this as a "system" installation
```

### 4. Test Plugin Commands

Use the installed deepctl binary directly:

```bash
# Basic test
/tmp/homebrew-test/brew-python/bin/deepctl --version

# Check installation detection
/tmp/homebrew-test/brew-python/bin/deepctl plugin list --verbose
# Should show "Installation method: system"
# Should mention "Using isolated plugin environment"

# Search for plugins
/tmp/homebrew-test/brew-python/bin/deepctl plugin search

# Install a plugin
/tmp/homebrew-test/brew-python/bin/deepctl plugin install deepctl-plugin-example
# This should create ~/.deepctl/plugins/venv/

# Verify plugin installation
ls -la ~/.deepctl/plugins/
# Should see venv/ and plugins.json

# List plugins
/tmp/homebrew-test/brew-python/bin/deepctl plugin list
# Should show the installed plugin

# Run the plugin
/tmp/homebrew-test/brew-python/bin/deepctl example

# Remove the plugin
/tmp/homebrew-test/brew-python/bin/deepctl plugin remove deepctl-plugin-example -y
```

### 5. Alternative: Test with System Python

If you have a system Python (like macOS's built-in Python), you can test more realistically:

```bash
# Install to system Python's user directory (DO NOT use sudo)
/usr/bin/python3 -m pip install --user dist/deepctl-*.whl

# The binary will be in user's local bin
~/.local/bin/deepctl plugin list --verbose
# Should detect as "system" installation

# Test plugin installation
~/.local/bin/deepctl plugin install deepctl-plugin-example

# Uninstall when done
/usr/bin/python3 -m pip uninstall deepctl
```

### 6. Using a Standalone Binary

If you build a standalone binary (e.g., with PyInstaller):

```bash
# Build standalone binary
pip install pyinstaller
pyinstaller --onefile src/deepctl/main.py --name deepctl

# The binary will be in dist/
./dist/deepctl plugin list --verbose
# Should detect as "system" installation

# Test plugin commands
./dist/deepctl plugin search
./dist/deepctl plugin install deepctl-plugin-example
```

## What to Verify

1. **Installation Detection**: `plugin list --verbose` should show "Installation method: system"
2. **Isolated Environment**: Installing plugins should create `~/.deepctl/plugins/venv/`
3. **Plugin State File**: Check `~/.deepctl/plugins/plugins.json` exists after installation
4. **Plugin Discovery**: Both built-in and external plugins should appear in `plugin list`
5. **Plugin Execution**: Installed plugins should be executable

## Troubleshooting

### Installation Not Detected as System

If the installation is detected as "pip" or "unknown" instead of "system":

```bash
# Check if the Python executable is writable
ls -la /path/to/python/bin/python3
# Should show no write permissions for user

# Check parent directory
ls -la /path/to/python/
# Should also be read-only
```

### Plugin Environment Not Created

If `~/.deepctl/plugins/venv/` is not created:

1. Check installation method detection is working
2. Ensure you have write permissions to `~/.deepctl/`
3. Check for error messages during plugin installation

### Cleanup

After testing:

```bash
# Remove test environment
chmod -R u+w /tmp/homebrew-test/brew-python  # Restore write permissions
rm -rf /tmp/homebrew-test

# Remove plugin environment (optional)
rm -rf ~/.deepctl/plugins/

# If you installed to user's system Python
/usr/bin/python3 -m pip uninstall deepctl
```

## Real Homebrew Testing

Once you're ready to test with actual Homebrew:

1. Create a Homebrew formula that installs the wheel
2. Install it locally with `brew install --build-from-source ./deepctl.rb`
3. Test the same plugin commands

The behavior should be identical to the simulated tests above.
