# Homebrew Testing

This directory contains scripts and tools for testing deepctl with Homebrew locally, without needing to publish to a real Homebrew tap.

## Quick Start

```bash
# 1. Build standalone binary
make build-binary

# 2. Generate Homebrew formula
make tap

# 3. Install via Homebrew
make tap-install

# 4. Test the installation
deepctl --version
deepctl plugin list --verbose  # Should show "Installation method: system"
deepctl plugin search
deepctl plugin install deepctl-plugin-example

# 5. Clean up when done
make tap-uninstall
```

## What Each Command Does

### `make build-binary`

- Uses PyInstaller to create a standalone `deepctl` binary
- Creates a tarball (`deepctl-0.1.8-macos-arm64.tar.gz`)
- Calculates SHA256 hash for the formula
- Outputs everything to `homebrew/dist/`

### `make tap`

- Generates a Homebrew formula (`deepctl.rb`)
- Uses the binary and SHA256 from `build-binary`
- Creates a `file://` URL pointing to the local tarball
- Formula includes basic tests

### `make tap-install`

- Checks that deepctl isn't already installed
- Warns about conflicts with other installation methods
- Installs using `brew install --formula ./deepctl.rb`
- Runs basic tests to verify installation

### `make tap-uninstall`

- Uninstalls deepctl from Homebrew
- Checks for other installations (pip, pipx, etc.)
- Provides helpful feedback about remaining installations

## Directory Structure

```
homebrew/
├── README.md           # This file
├── scripts/
│   ├── build-binary.sh    # Build standalone binary
│   ├── generate-tap.sh    # Generate Homebrew formula
│   ├── tap-install.sh     # Install via Homebrew
│   └── tap-uninstall.sh   # Uninstall from Homebrew
└── dist/               # Generated files (created by scripts)
    ├── deepctl         # Standalone binary
    ├── deepctl-0.1.8-macos-arm64.tar.gz  # Tarball for Homebrew
    ├── deepctl.sha256  # SHA256 hash
    └── deepctl.rb      # Homebrew formula
```

## Testing Plugin System

The key benefit of this approach is testing that the plugin system works correctly with a Homebrew installation:

1. **Installation Detection**: Should detect as "system" installation
2. **Plugin Environment**: Should create isolated plugin environment at `~/.deepctl/plugins/`
3. **Plugin Commands**: All `deepctl plugin` commands should work
4. **Plugin Isolation**: Plugins should not interfere with the main installation

## Troubleshooting

### Binary Build Fails

- Ensure you're in the project root directory
- Check that PyInstaller installs correctly: `uv pip install pyinstaller`
- Try cleaning build artifacts: `rm -rf homebrew/build homebrew/dist`

### Formula Generation Fails

- Run `make build-binary` first
- Check that `homebrew/dist/deepctl` and `homebrew/dist/deepctl.sha256` exist

### Installation Conflicts

- The install script will warn about existing deepctl installations
- Consider uninstalling other versions first:
  - pip: `pip uninstall deepctl`
  - pipx: `pipx uninstall deepctl`
  - uv: `uv tool uninstall deepctl`

### Plugin System Issues

- Check installation method: `deepctl plugin list --verbose`
- Should show "Installation method: system"
- Plugin environment should be at `~/.deepctl/plugins/venv/`

## Comparison with Real Homebrew

This local testing setup behaves identically to a real Homebrew installation:

- Binary installed to `/opt/homebrew/bin/deepctl` (or `/usr/local/bin/`)
- Detected as "system" installation by the plugin manager
- Plugin isolation works the same way
- All plugin commands function identically

The only difference is using a local `file://` URL instead of downloading from GitHub releases.
