# Testing With Flexible Options

This document describes the flexible testing options available in the deepctl monorepo.

## Overview

The deepctl project uses a custom test runner that extends pytest with additional options for running tests across the monorepo structure. This allows you to easily run tests for the main CLI, all packages, or specific packages.

## Available Commands

### Default: Run Main CLI Tests Only

```bash
uv run pytest
```

This runs only the tests in `./tests/` directory, which contains tests for the main deepctl CLI.

### Run All Tests Across the Workspace

```bash
uv run pytest --all
```

This runs all tests across the entire workspace, including:

- Main CLI tests in `./tests/`
- All package tests in `packages/*/tests/`

### Run Tests for Specific Package(s)

```bash
# Single package
uv run pytest --package=deepctl-core

# Multiple packages (comma-separated)
uv run pytest --package=deepctl-core,deepctl-cmd-login
```

This runs tests only for the specified package(s).

## Additional Options

All standard pytest options work with these commands:

### Filter by Markers

```bash
# Run only unit tests
uv run pytest -m unit

# Run only integration tests
uv run pytest --all -m integration
```

### Filter by Pattern

```bash
# Run tests matching a pattern
uv run pytest -k test_auth

# Combine with package selection
uv run pytest --package=deepctl-core -k test_verify
```

### Coverage Options

```bash
# Run without coverage
uv run pytest --no-cov

# Run all tests without coverage
uv run pytest --all --no-cov
```

### Verbose Output

```bash
# Verbose output
uv run pytest -v

# Very verbose output
uv run pytest -vv
```

## Direct Test Runner Usage

You can also use the test runner script directly:

```bash
# Show help
uv run python scripts/test_runner.py --help

# Run with the same options
uv run python scripts/test_runner.py --all
uv run python scripts/test_runner.py --package=deepctl-core
```

## Coverage Reports

By default, tests run with coverage enabled. Coverage reports are:

- Displayed in the terminal with missing lines
- Generated as HTML in `htmlcov/index.html`

The coverage scope automatically adjusts based on what you're testing:

- Default (`uv run pytest`): Coverage for `deepctl` only
- With `--all`: Coverage for all packages
- With `--package=X`: Coverage for the specified package(s)

## Available Packages

The following packages have tests available:

- `deepctl-core` - Core functionality
- `deepctl-cmd-login` - Login command
- `deepctl-cmd-projects` - Projects command
- `deepctl-cmd-transcribe` - Transcribe command
- `deepctl-cmd-usage` - Usage command
- `deepctl-shared-utils` - Shared utilities

Note: The `deepctl-plugin-example` package is excluded from `--all` runs by default.

## Implementation Details

The flexible testing is implemented through:

1. A custom test runner script at `scripts/test_runner.py`
2. A root `conftest.py` that adds the custom pytest options
3. The existing `pytest.ini` configuration

When you use `--all` or `--package` options, pytest automatically delegates to the test runner script, which builds the appropriate pytest command with the correct test paths and coverage configuration.
