# GitHub Actions and Makefile Integration

## Overview

Our GitHub Actions workflows use the same Makefile commands as local development for consistency and reliability.

## Release Workflow (.github/workflows/release.yml)

The release workflow mirrors the local release process:

```yaml
# Workflow steps:
1. make verify-packages  # Pre-build verification
2. make build           # Build all packages
3. make verify-packages # Post-build verification
4. Test installation    # Direct pip install test
5. Publish to PyPI      # Using scripts/publish.py
```

### Why This Approach?

- **Consistency**: Same commands work locally and in CI
- **Single Source of Truth**: Build logic lives in one place
- **Easy Debugging**: Run the exact same commands locally
- **Fail Fast**: Verification before and after build

## Test Workflow (.github/workflows/test.yml)

The test workflow also uses Makefile commands:

```yaml
# Build test job:
- make build # Build packages
- make verify-packages # Verify configuration
```

## Local vs CI Differences

### What's the Same:

- `make build` - Builds all packages
- `make verify-packages` - Verifies configuration
- Build process and verification logic

### What's Different:

- **No version/commit/tag steps in CI** - Tag already exists when workflow runs
- **Publishing** - CI uses environment variables and trusted publishing
- **Installation testing** - CI tests actual pip install process

## Benefits

1. **Reproducible Builds**: Same build process everywhere
2. **Easier Maintenance**: Update Makefile, CI automatically uses new logic
3. **Better Testing**: Can test exact CI commands locally
4. **Consistency**: Developers and CI use same tooling

## Testing CI Locally

You can test the exact commands CI runs:

```bash
# Test what the verify job does
make verify-packages

# Test what the build job does
make build
make verify-packages

# Test the full local release (which triggers CI)
make release
```

## Workflow Triggers

- **Release Workflow**: Triggered by version tags (`v*`)
- **Test Workflow**: Triggered by pushes to main and PRs

## Example: Full Release Process

1. **Locally**:

   ```bash
   make release        # Version, commit, build, verify, tag
   git push --tags     # Triggers CI
   ```

2. **In CI**:
   ```bash
   make verify-packages  # Verify
   make build           # Build
   make verify-packages # Verify again
   # Then test and publish
   ```

The CI workflow is essentially the build/verify/publish part of the local release, since versioning and tagging already happened locally.
