# Makefile Commands Reference

All development and release tasks should be performed using the Makefile commands.

## Quick Reference

```bash
make help              # Show all available commands
```

## Development Commands

### Setup

```bash
make dev-setup         # Set up complete development environment
make install           # Install runtime dependencies only
make install-dev       # Install all development dependencies
```

### Code Quality

```bash
make format            # Auto-format code with black
make format-check      # Check code formatting (no changes)
make lint              # Run all linters via tox
make lint-fix          # Run ruff with auto-fix
make lint-check        # Run ruff without fixes
make typecheck         # Run mypy type checker
make quality           # Run all quality checks
```

### Testing

```bash
make test              # Run tests with pytest (development mode)
make test-quick        # Run tests quickly (no coverage)
make test-verbose      # Run tests with verbose output
make test-full         # Run tests on all Python versions using tox
make test-parallel     # Run all tox environments in parallel
make ci                # Run full CI pipeline
```

### Building

```bash
make build             # Build all packages
make clean             # Clean all build artifacts and caches
```

## Release Commands

### Standard Release Process

```bash
make release           # Automated release (with [no-ci])
make release-manual    # Manual release (without [no-ci])
```

### Individual Steps

```bash
make version           # Update version in all packages
make version VERSION=0.2.0  # Update to specific version

make commit            # Commit changes
make commit NOCI=1     # Commit with [no-ci] flag

make verify-packages   # Verify all packages are properly configured
make tag               # Create git tag for current version
```

### Publishing

```bash
make publish-test      # Publish to TestPyPI
make publish           # Publish to PyPI (use with caution!)
```

## Running the CLI

```bash
make run               # Run the CLI (show help)
make run-version       # Show CLI version
```

## Utility Commands

```bash
make info              # Show project information
make docs-list         # List documentation files
make pre-commit-install # Install pre-commit hooks
make pre-commit-run    # Run pre-commit on all files
```

## Aliases

For convenience, these short aliases are available:

```bash
make t   # Alias for test
make tc  # Alias for test-full (tox complete)
make tf  # Alias for test-parallel (tox fast)
make tl  # Alias for lint
make q   # Alias for check (quick quality check)
make f   # Alias for format
make l   # Alias for lint
```

## Release Workflow Example

```bash
# Standard release
make release
# Enter version: 0.2.0
git push origin main --tags

# Or step by step
make version VERSION=0.2.0
make commit NOCI=1
make build
make verify-packages
make tag
git push origin main --tags
```

## Tips

1. Always run `make help` to see the latest commands
2. Use `make verify-packages` before releases
3. The Makefile ensures consistency between local development and CI
4. All CI workflows use the same make commands
