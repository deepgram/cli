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

See [Testing With Flexible Options](Testing%20With%20Flexible%20Options.md) for detailed test runner usage.

Quick commands:

```bash
# Run main CLI tests only (default)
uv run pytest

# Run all tests across workspace
uv run pytest --all

# Run specific package tests
uv run pytest --package=deepctl-core

# Run with coverage report
uv run pytest --cov
```

## Test Requirements

- All new features must include tests
- Bug fixes should include regression tests
- Tests must be cross-platform compatible
- Use pytest fixtures for common setup
- Mock external services appropriately

## CI/CD Testing

GitHub Actions runs tests on:

- Multiple Python versions (3.8-3.12)
- Multiple platforms (Linux, Windows, macOS)
- Both x86_64 and ARM architectures
