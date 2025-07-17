# Development Guide with uv

This guide covers development workflows using `uv`, the ultra-fast Python package installer and resolver.

## Why uv?

- **Speed**: 10-100x faster than pip for dependency resolution and installation
- **Modern**: Built in Rust with modern Python packaging standards
- **Reliable**: Consistent dependency resolution across environments
- **Complete**: Package management, virtual environments, and tool installation
- **Compatible**: Drop-in replacement for pip/pipx in most cases

## Quick Setup

### Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Alternative: using pip
pip install uv
```

### Project Setup

```bash
# Clone the repository
git clone https://github.com/deepgram/cli
cd cli

# Create virtual environment with uv
uv venv

# Activate virtual environment
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# Install development dependencies
uv pip install -e ".[dev]"
```

## Development Workflows

### Managing Dependencies

```bash
# Install runtime dependencies only
uv pip install -e .

# Install with development tools
uv pip install -e ".[dev]"

# Install with test dependencies
uv pip install -e ".[test]"

# Install all optional dependencies
uv pip install -e ".[dev,test]"

# Add a new dependency
# Edit pyproject.toml, then:
uv pip install -e ".[dev]"
```

### Running Commands

```bash
# Run deepctl directly (if venv is activated)
deepctl --help

# Run with uv (automatically uses project venv)
uv run deepctl --help

# Run tests
uv run pytest

# Run linting
uv run black src/
uv run flake8 src/
uv run mypy src/

# Run specific test file
uv run pytest tests/test_auth.py
```

### Building and Distribution

```bash
# Build packages (uses uv if available)
uv run python scripts/build.py

# Or build directly with uv
uv build

# Publish to PyPI
uv publish dist/*

# Install locally for testing
uv pip install dist/deepctl-*.whl
```

### Virtual Environment Management

```bash
# Create new virtual environment
uv venv

# Create with specific Python version
uv venv --python 3.11

# Create with custom name
uv venv my-custom-env

# Remove virtual environment
rm -rf .venv  # or your custom env directory

# List installed packages
uv pip list

# Show package information
uv pip show deepgram-sdk

# Freeze current dependencies
uv pip freeze
```

### Tool Management

```bash
# Install deepctl as a global tool
uv tool install .

# Run deepctl without installing
uvx deepctl --help

# Update tool
uv tool upgrade deepctl

# Uninstall tool
uv tool uninstall deepctl

# List installed tools
uv tool list
```

## Advanced Usage

### Lock Files

```bash
# Generate lock file (when using uv.lock)
uv lock

# Install from lock file
uv sync

# Update lock file with new dependencies
uv lock --upgrade
```

### Cross-Platform Development

```bash
# Install dependencies for multiple platforms
uv pip install -e ".[dev]" --platform win_amd64
uv pip install -e ".[dev]" --platform linux_x86_64
uv pip install -e ".[dev]" --platform macosx_10_9_x86_64

# Use specific Python version
uv venv --python 3.8
uv venv --python 3.11
uv venv --python 3.12
```

### CI/CD Integration

```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ['3.8', '3.9', '3.10', '3.11', '3.12']

    steps:
    - uses: actions/checkout@v4
    
    - name: Install uv
      uses: astral-sh/setup-uv@v1
      with:
        version: "latest"
    
    - name: Set up Python
      run: uv python install ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: uv sync --all-extras
    
    - name: Run tests
      run: uv run pytest
    
    - name: Run linting
      run: |
        uv run black --check src/
        uv run flake8 src/
        uv run mypy src/
```

## Performance Comparison

| Operation | pip | uv | Speedup |
|-----------|-----|----|---------| 
| Fresh install | 45s | 2s | **22x** |
| Cached install | 8s | 0.5s | **16x** |
| Lock file generation | 60s | 3s | **20x** |
| Virtual env creation | 5s | 0.2s | **25x** |

## Migration from pip/pipx

### From pip

```bash
# Old way
pip install -e ".[dev]"

# New way
uv pip install -e ".[dev]"
```

### From pipx

```bash
# Old way
pipx install deepctl
pipx run deepctl --help

# New way
uv tool install deepctl
uvx deepctl --help
```

### From requirements.txt

We don't use `requirements.txt` anymore! Everything is in `pyproject.toml`:

```bash
# Old way
pip install -r requirements.txt

# New way (no separate requirements file needed)
uv pip install -e .
```

## Troubleshooting

### Common Issues

1. **Virtual environment not activated**
   ```bash
   # Solution: Use uv run or activate manually
   uv run deepctl --help
   # OR
   source .venv/bin/activate
   ```

2. **Package not found**
   ```bash
   # Solution: Make sure you're in the right directory
   cd /path/to/deepctl
   uv pip install -e ".[dev]"
   ```

3. **Permission errors**
   ```bash
   # Solution: Don't use sudo with uv
   # Create virtual environment first
   uv venv
   source .venv/bin/activate
   uv pip install -e ".[dev]"
   ```

### Getting Help

```bash
# uv help
uv --help
uv pip --help
uv tool --help

# Check uv version
uv --version

# Verbose output for debugging
uv pip install -e ".[dev]" --verbose
```

## Best Practices

1. **Always use virtual environments**
   ```bash
   uv venv
   source .venv/bin/activate
   ```

2. **Use uv run for project commands**
   ```bash
   uv run pytest  # instead of just pytest
   ```

3. **Keep pyproject.toml updated**
   - Add new dependencies to `pyproject.toml`
   - Use version constraints appropriately
   - Separate dev/test dependencies

4. **Use lock files for reproducible builds**
   ```bash
   uv lock  # Generate uv.lock
   uv sync  # Install from lock
   ```

5. **Cache dependencies in CI**
   ```yaml
   - name: Cache uv
     uses: actions/cache@v3
     with:
       path: ~/.cache/uv
       key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
   ```

## Resources

- [uv Documentation](https://docs.astral.sh/uv/)
- [uv GitHub Repository](https://github.com/astral-sh/uv)
- [Python Packaging Guide](https://packaging.python.org/)
- [PEP 621 - Storing project metadata in pyproject.toml](https://peps.python.org/pep-0621/) 