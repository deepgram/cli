# deepctl

The official Deepgram CLI.

## Quick Start

### Try it without installing (like `npx`)

```bash
# Using pipx (traditional)
pipx run deepctl --help
pipx run deepctl transcribe audio.wav

# Using uv (recommended - much faster!)
uvx deepctl --help
uvx deepctl transcribe audio.wav
```

### Install permanently

```bash
# Recommended: Using pipx (supports plugins!)
pipx install deepctl

# Alternative: Using uv tool (plugins require manual workarounds)
uv tool install deepctl
```

## Usage

The CLI provides multiple command aliases for flexibility:

- `deepctl` - Primary command
- `deepgram` - Alternative command
- `dg` - Short alias

### Basic Commands

```bash
# Authentication
deepctl login

# Transcribe audio
deepctl transcribe audio.wav
deepctl transcribe https://example.com/audio.mp3

# Manage projects
deepctl projects list
deepctl projects create "My Project"

# View usage statistics
deepctl usage --month 2024-01
```

### Configuration

The CLI supports multiple configuration methods:

1. Command-line arguments (highest priority)
2. Environment variables
3. User config file (`~/.deepgram/config.yaml`)
4. Project config file (`./deepgram.yaml`)

### Output Formats

Choose your preferred output format:

```bash
deepctl transcribe audio.wav --output json
deepctl transcribe audio.wav --output yaml
deepctl transcribe audio.wav --output table
deepctl transcribe audio.wav --output csv
```

## Development

This CLI is built with Python and uses a modular plugin architecture. **Cross-platform compatibility** is a core requirement - the CLI must work identically on Linux, Windows, macOS (Intel), and macOS (Apple Silicon).

> **Important:** All development and release tasks should be performed using `make` commands. This ensures consistency across different environments and with our CI/CD pipeline.

### Requirements

- Python 3.10+
- `uv`
- Works on all major platforms:
  - Linux (x86_64, arm64)
  - Windows (x86_64)
  - macOS (Intel x86_64, Apple Silicon arm64)

### Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Installation for Development

```bash
git clone https://github.com/deepgram/cli
cd cli

# Create virtual environment and install dependencies
uv venv
uv pip install -e ".[dev]"
```

### Dependencies

All dependencies are managed in `pyproject.toml`. Install them with:

```bash
uv pip install -e .              # Runtime dependencies
uv pip install -e ".[dev]"       # Development dependencies
uv pip install -e ".[test]"      # Test dependencies
```

### Workspace Structure

This repository is organized as a uv workspace (monorepo) to support multiple related packages:

```
cli/                    # Workspace root
├── src/               # Main CLI package (deepctl)
│   └── deepgram_cli/
├── packages/          # Additional workspace packages
│   └── (future packages)
└── docs/              # Shared documentation
```

See [Workspace and Monorepo Architecture](docs/Workspace%20and%20Monorepo%20Architecture.md) for detailed information about the workspace structure and how to add new packages.

### Development Workflow

All development tasks should be performed using the Makefile:

```bash
make help              # Show all available commands
make test              # Run tests
make format            # Format code
make lint              # Run linters
make build             # Build packages
```

See [Makefile Commands Reference](docs/Makefile%20Commands%20Reference.md) for the complete list of commands.

## Release Process

### Automated Release (Recommended)

The standard way to create a release:

```bash
make release
# Enter version when prompted (e.g., 0.2.0)
# This will:
# 1. Update all package versions
# 2. Commit changes with [no-ci]
# 3. Build all packages
# 4. Verify configuration
# 5. Create git tag

# Then push to trigger the release:
git push origin main --tags
```

### Manual Release Process

If you need more control over the release process:

```bash
make release-manual
# Enter version when prompted
# Same as above but commits without [no-ci]
# Useful if you want CI to verify before pushing the tag
```

### Individual Release Steps

For complete control, run each step separately:

```bash
# 1. Update versions
make version VERSION=0.2.0

# 2. Commit changes
make commit         # Normal commit
# or
make commit NOCI=1  # With [no-ci] flag

# 3. Build packages
make build

# 4. Verify everything is correct
make verify-packages

# 5. Create tag
make tag

# 6. Push to GitHub
git push origin main --tags
```

### Release Verification

Before any release, you can verify the configuration:

```bash
make verify-packages
```

This checks:

- All packages are properly configured
- Build scripts include all packages
- Version scripts include all packages
- Dependencies are correctly set
- Packages have been built

### What Happens After Push

When you push a version tag (e.g., `v0.2.0`), GitHub Actions automatically:

1. Verifies package configuration
2. Builds all packages
3. Tests installation
4. Publishes to PyPI

## Plugin Support

### Installing Plugins

Deepctl supports external plugins that add custom commands:

```bash
# First, install deepctl globally with pipx
pipx install deepctl

# Then inject plugins into the same environment
pipx inject deepctl deepctl-plugin-example
pipx inject deepctl your-custom-plugin
```

### Creating Plugins

Create custom commands by extending the `BaseCommand` class:

```python
from deepctl_core.base_command import BaseCommand

class MyCommand(BaseCommand):
    name = "mycommand"
    help = "Description of my command"

    def handle(self, config, auth_manager, client, **kwargs):
        # Command implementation
        pass
```

See [packages/deepctl-plugin-example](packages/deepctl-plugin-example) for a complete example.

## Support

- [Documentation](https://developers.deepgram.com/docs/cli)
- [Community Discord](https://discord.gg/deepgram)
- [Bug Reports](https://github.com/deepgram/cli/issues)

## License

MIT License - see LICENSE file for details.
