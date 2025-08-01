# Hybrid Homebrew Distribution Strategy

## Overview

deepctl provides two Homebrew installation options to serve different user needs:

1. **Standard Installation** (`deepctl`) - Wheel-based, uses Homebrew's Python
2. **Standalone Installation** (`deepctl-standalone`) - Self-contained binary

This hybrid approach combines the benefits of both distribution strategies while giving users choice based on their requirements.

## Installation Options

### Option 1: Standard Installation (Recommended)

```bash
brew install deepctl
```

**What you get:**

- Uses your existing Homebrew Python installation
- Installs deepctl and all dependencies as Python packages
- Smaller installation footprint
- Maximum compatibility with Python ecosystem
- Automatic updates through Homebrew

**Best for:**

- Most users
- Development environments
- Users who already have Python/Homebrew Python
- Users who want the most tested and compatible version

### Option 2: Standalone Installation

```bash
brew install deepctl-standalone
```

**What you get:**

- Single self-contained binary with all dependencies bundled
- No Python dependency required
- Larger installation size
- Completely isolated from system Python

**Best for:**

- Users without Python installed
- Minimal system environments
- Users who prefer self-contained binaries
- CI/CD environments where you want zero external dependencies

## Build Process

### For Maintainers

The build process supports both distribution methods:

```bash
# Build both Homebrew formulas
make build-homebrew

# Or build individually:
make build-homebrew-wheels    # Wheel-based formula
make build-homebrew-binary    # Binary-based formula
```

### Standard Formula (Wheel-based)

The standard formula:

- Uses our existing PyPI wheel builds
- Leverages Homebrew's `virtualenv_install_with_resources`
- Installs all deepctl packages as separate Python packages
- Uses the same build process as our PyPI releases

### Standalone Formula (Binary-based)

The standalone formula:

- Uses PyInstaller to create a self-contained binary
- Bundles Python interpreter and all dependencies
- Creates a single executable file
- Uses our existing `homebrew/scripts/build-binary.sh` process

## Technical Details

### Why This Approach?

1. **Leverages Existing Infrastructure**: The wheel-based approach uses our proven PyPI build process
2. **User Choice**: Different users have different needs and preferences
3. **Risk Mitigation**: If one approach has issues, users can fall back to the other
4. **Future-Proofing**: Can phase out approaches that don't work well over time

### Plugin System Compatibility

Both installation methods fully support the plugin system:

- **Standard**: Plugins install into the same Python environment as deepctl
- **Standalone**: Plugins install into an isolated environment at `~/.deepctl/plugins/venv`

Both approaches automatically detect the installation method and configure plugin handling appropriately.

### Build Strategy Comparison

| Aspect            | Standard (Wheels)            | Standalone (Binary)           |
| ----------------- | ---------------------------- | ----------------------------- |
| **Build Tool**    | uv build                     | PyInstaller                   |
| **Build Time**    | Fast (~30 seconds)           | Slow (~2-3 minutes)           |
| **Output Size**   | Small (dependencies shared)  | Large (~120MB)                |
| **Dependencies**  | Python required              | Self-contained                |
| **Compatibility** | Maximum (uses proven wheels) | Good (some edge cases)        |
| **Maintenance**   | Low (reuses PyPI process)    | Higher (separate build logic) |
| **Updates**       | Automatic via Homebrew       | Manual binary rebuilds        |

## Usage Examples

Both installations provide identical CLI interfaces:

```bash
# Both work the same way
deepctl --version
deepctl login
deepctl transcribe audio.wav
deepctl plugin install some-plugin
```

The only difference is how they're installed and their internal architecture.

## Migration Between Versions

Users can switch between versions:

```bash
# Switch from standard to standalone
brew uninstall deepctl
brew install deepctl-standalone

# Switch from standalone to standard
brew uninstall deepctl-standalone
brew install deepctl
```

## Development Workflow

### Local Testing

```bash
# Test wheel-based formula (requires PyPI publish first)
make build-homebrew-wheels
# Formula will be at homebrew/dist/deepctl.rb

# Test binary-based formula
make build-homebrew-binary
# Formula will be at homebrew/dist/deepctl-standalone.rb
```

### Release Process

1. **Standard Release Process**: Our existing `make release` process handles PyPI publishing
2. **Wheel Formula**: Generated automatically after PyPI publish
3. **Binary Formula**: Generated from local binary build
4. **Both formulas** can be submitted to Homebrew tap simultaneously

## Future Considerations

### Potential Improvements

1. **Modern Tooling**: Could migrate to `uv` + `packaged` for standalone builds
2. **Cross-Platform**: Extend to Linux and Windows package managers
3. **Automated SHA256**: Fetch PyPI SHA256s automatically for wheel formula
4. **CI Integration**: Automate formula generation in GitHub Actions

### Deprecation Path

If one approach proves significantly better:

- Keep both for at least 2 major versions
- Provide clear migration documentation
- Deprecate with plenty of advance notice

## Troubleshooting

### Standard Installation Issues

If the wheel-based installation has problems:

```bash
brew uninstall deepctl
brew install deepctl-standalone  # Fallback to binary
```

### Standalone Installation Issues

If the binary-based installation has problems:

```bash
brew uninstall deepctl-standalone
brew install deepctl  # Fallback to wheels
```

### Plugin Issues

Both versions support plugins, but they install differently:

- Standard: `pip list` shows installed plugins
- Standalone: `deepctl plugin list` shows installed plugins

## Summary

The hybrid approach gives deepctl users the best of both worlds:

- **Most users** get the reliable, fast, wheel-based installation
- **Advanced users** get the self-contained binary option
- **Maintainers** can leverage existing build infrastructure while providing choice
- **Future flexibility** to evolve based on user feedback and ecosystem changes
