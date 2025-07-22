# Testing With Tox

This document describes how to run tests in the deepctl monorepo using tox.

## Overview

The deepctl project uses tox for testing across multiple Python versions and environments. All test configuration is centralized in `pyproject.toml` under the `[tool.tox]` section.

## Running Tests

### Test with all Python versions

```bash
uv run tox
```

This runs tests on all configured Python versions (3.10, 3.11, 3.12) and the linting environment. Missing Python versions are automatically skipped.

### Test with a specific Python version

```bash
# Test with Python 3.11
uv run tox -e py311

# Test with Python 3.10
uv run tox -e py310

# Test with Python 3.12
uv run tox -e py312
```

### Run linting only

```bash
uv run tox -e lint
```

This runs:

- Black (code formatting check)
- Flake8 (code style)
- MyPy (type checking)

### Run tests in parallel

```bash
# Run all environments in parallel
uv run tox -p

# Run with specific number of workers
uv run tox -p 4
```

## Test Coverage

Tests automatically run with coverage enabled. After running tests, you'll find:

- Coverage report in the terminal showing missing lines
- HTML coverage report in `htmlcov/index.html`

Coverage includes:

- Main deepctl CLI
- All command packages
- Core and shared utility packages

## Tox Configuration

All tox configuration is in `pyproject.toml` under `[tool.tox]`. The configuration:

1. Sets up isolated environments for each Python version
2. Uses `uv` for fast dependency installation
3. Installs all workspace packages in editable mode
4. Runs pytest with full coverage across all packages

## Direct pytest usage (for development)

During development, you can still run pytest directly for faster feedback:

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/test_main.py

# Run tests with specific marker
uv run pytest -m unit

# Run tests matching a pattern
uv run pytest -k test_auth
```

Note: Direct pytest usage won't have the same isolation as tox and will only test with your current Python version.

## Available Test Markers

Tests can be marked with:

- `unit` - Unit tests
- `integration` - Integration tests
- `slow` - Slow running tests
- `requires_auth` - Tests that require authentication
- `requires_network` - Tests that require network access

## Python Version Support

The project supports and tests against:

- Python 3.10
- Python 3.11
- Python 3.12

All packages maintain compatibility across these versions.
