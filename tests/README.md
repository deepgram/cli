# deepctl Tests

This directory contains tests for the main deepctl entry point (`src/deepctl/`).

## Test Organization

```
tests/
├── unit/        # Unit tests for individual modules
├── integration/ # Integration tests for end-to-end workflows
└── fixtures/    # Test data and fixtures
```

## Package Test Structure

- This directory **only** tests code in `src/deepctl/`
- Each package in `packages/` contains its own test suite
- Example: `packages/deepctl-cmd-login/tests/`

## Running Tests

```bash
# Run all tests in this directory
uv run pytest tests/

# Run with coverage for the main package
uv run pytest tests/ --cov=src/deepctl

# Run a specific test file
uv run pytest tests/unit/test_main.py
```

For running all tests across the workspace, see the main README.
