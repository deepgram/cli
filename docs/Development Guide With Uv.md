# Development Guide with uv

This guide covers development workflows using `uv`, the ultra-fast Python package installer.

## Why uv?

- **Speed**: 10-100x faster than pip
- **Modern**: Built in Rust with modern Python packaging standards
- **Reliable**: Consistent dependency resolution
- **Complete**: Package management, virtual environments, and tools

## Quick Setup

### Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Project Setup

```bash
# Clone and setup
git clone https://github.com/deepgram/cli
cd cli

# Create virtual environment and install
uv venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install with development dependencies
uv pip install -e ".[dev]"
```

## Common Workflows

### Running Commands

```bash
# With activated venv
deepctl --help

# Without activating (uv automatically uses project venv)
uv run deepctl --help

# Run tests
uv run pytest
uv run pytest --package=deepctl-core  # Specific package

# Linting and formatting
uv run black src/
uv run mypy src/
```

### Dependency Management

```bash
# Install runtime only
uv pip install -e .

# Install with dev tools
uv pip install -e ".[dev]"

# After editing pyproject.toml
uv pip install -e ".[dev]"
```

### Building

```bash
# Build all packages
uv build

# Or use the build script
uv run python scripts/build.py
```

### Tool Management

```bash
# Install as global tool
uv tool install .

# Run without installing
uvx deepctl --help

# Update/uninstall
uv tool upgrade deepctl
uv tool uninstall deepctl
```

## Virtual Environment Management

```bash
# Create with specific Python
uv venv --python 3.11

# Remove
rm -rf .venv

# List packages
uv pip list

# Show package info
uv pip show deepgram-sdk
```

## CI/CD Integration

```yaml
# .github/workflows/test.yml
- name: Install uv
  uses: astral-sh/setup-uv@v1

- name: Install dependencies
  run: uv sync --all-extras

- name: Run tests
  run: uv run pytest
```

## Migration from pip/pipx

```bash
# Old: pip install -e ".[dev]"
# New: uv pip install -e ".[dev]"

# Old: pipx install deepctl
# New: uv tool install deepctl

# Old: pipx run deepctl
# New: uvx deepctl
```

## Troubleshooting

**Virtual environment not activated?**

```bash
uv run deepctl --help  # uv handles it automatically
```

**Package not found?**

```bash
cd /path/to/cli
uv pip install -e ".[dev]"
```

**Permission errors?**

```bash
# Don't use sudo - create venv first
uv venv
source .venv/bin/activate
```

## Resources

- [uv Documentation](https://docs.astral.sh/uv/)
- [uv GitHub](https://github.com/astral-sh/uv)
