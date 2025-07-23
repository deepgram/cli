# Testing and Test Strategy

## Overview

deepctl follows a comprehensive testing strategy across its monorepo structure to ensure reliability and maintainability.

## Test Organization

```
cli/
├── tests/                    # Main CLI integration tests
└── packages/
    └── */tests/unit/         # Package-specific unit tests
```

## Testing Approach

### Unit Tests

- Each package contains its own unit tests
- Focus on testing individual components in isolation
- Mock external dependencies (API calls, file system)
- Aim for high coverage (>80%)

### Integration Tests

- Located in root `tests/` directory
- Test complete command workflows
- Verify package interactions
- Test actual CLI invocations

## Running Tests

### Using Tox (Recommended for CI and Multi-Version Testing)

Tox provides isolated test environments and ensures consistent test execution:

```bash
# Run tests for all Python versions
uv run tox

# Run tests for specific Python version
uv run tox -e py310
uv run tox -e py311
uv run tox -e py312

# Run linting
uv run tox -e lint

# Run specific environments
uv run tox -e py311,lint
```

### Using pytest directly (For Development)

For development, you can use the Makefile targets or run pytest directly:

```bash
# Using Makefile (recommended - includes custom pytest options)
make test         # Run main CLI tests only
make test-all     # Run all tests across workspace
make test-dev     # Run with verbose output and stop on first failure

# Direct pytest (basic usage)
uv run pytest                    # Run tests in current directory
uv run pytest tests/             # Run main CLI tests
uv run pytest packages/*/tests/  # Run all package tests

# Run specific package tests
uv run pytest packages/deepctl-core/tests/

# Run with coverage (requires pytest-cov)
uv run pytest --cov=deepctl --cov=deepctl_core --cov-report=term-missing
```

## Test Requirements

- All new features must include tests
- Bug fixes should include regression tests
- Tests must be cross-platform compatible
- Use pytest fixtures for common setup
- Mock external services appropriately

## CI/CD Testing

GitHub Actions runs tests on:

- Multiple Python versions (3.10-3.12)
- Multiple platforms (Linux, Windows, macOS)
- Both x86_64 and ARM architectures
