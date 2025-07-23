# Versioning Strategy

## Overview

deepctl uses synchronized versioning for all official packages to ensure compatibility and simplify the user experience. All official deepctl packages share the same version number and are released together.

## Version Policy

### Synchronized Packages

All official packages are versioned together:

- `deepctl` - Main CLI package
- `deepctl-core` - Core functionality
- `deepctl-shared-utils` - Shared utilities
- `deepctl-cmd-*` - Official command packages
- `deepctl-plugin-example` - Example plugin (for documentation/testing)

### Independent Packages

Community plugins version independently:

- `deepctl-plugin-*` - Third-party plugins (except the example)

## Semantic Versioning

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes (including Python version requirement changes)
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

## Release Process

### 1. Update Version

```bash
# Update all packages to new version
python scripts/version.py 0.2.0

# For pre-releases
python scripts/version.py 1.0.0-rc.1
```

### 2. Review Changes

```bash
# Review the changes
git diff

# Check which files were updated
git status
```

### 3. Commit and Tag

```bash
# Commit version bump
git add -A
git commit -m "Bump version to v0.2.0"

# Create annotated tag
git tag -a v0.2.0 -m "Release version 0.2.0"
```

### 4. Push to GitHub

```bash
# Push commits and tags
git push origin main
git push origin v0.2.0

# This triggers the automated release workflow
```

### 5. Monitor Release

The GitHub Actions workflow will:

1. Build all packages
2. Test the installation
3. Publish to PyPI

## Local Testing

### Build All Packages

```bash
# Build all distribution packages
python scripts/build.py

# Check the dist/ directory
ls -la dist/
```

### Test Installation

```bash
# Create a test virtual environment
python -m venv test-env
source test-env/bin/activate  # On Windows: test-env\Scripts\activate

# Install from local files
pip install dist/deepctl-0.2.0-py3-none-any.whl

# Test the CLI
deepctl --version
deepctl --help
```

### Publish to Test PyPI

```bash
# First time setup - create account at https://test.pypi.org
# Install twine if needed
pip install twine

# Upload to Test PyPI
python scripts/publish.py --test

# Test installation from Test PyPI
pip install --index-url https://test.pypi.org/simple/ deepctl
```

## Version Management Script

The `scripts/version.py` script handles:

- Updating version in all `pyproject.toml` files
- Updating internal dependency versions
- Updating `__version__` in `src/deepctl/__init__.py`

## Dependency Management

### Internal Dependencies

When a package depends on another deepctl package:

```toml
dependencies = [
    "deepctl-core>=0.2.0",  # Version is automatically updated
    "deepctl-shared-utils>=0.2.0",
]
```

### External Dependencies

External dependencies use standard version specifiers:

```toml
dependencies = [
    "click>=8.0.0",
    "rich>=13.0.0",
    "pydantic>=2.0.0",
]
```

## GitHub Actions Workflow

The `.github/workflows/release.yml` workflow:

1. Triggers on version tags (`v*`)
2. Builds all packages
3. Tests installation
4. Publishes to PyPI using trusted publishing

### Required Setup

1. **PyPI Account**: Create at https://pypi.org
2. **Trusted Publishing**: Configure in PyPI project settings
3. **GitHub Environment** (optional): Create `pypi` environment for additional protection

## Troubleshooting

### Version Conflicts

If users report version conflicts:

```bash
# Check installed versions
pip list | grep deepctl

# Force reinstall with specific version
pip install --force-reinstall deepctl==0.2.0
```

### Failed Releases

If a release fails:

1. Check GitHub Actions logs
2. Verify PyPI credentials/trusted publishing
3. Ensure version doesn't already exist
4. Try manual publishing: `python scripts/publish.py`

## Best Practices

1. **Version Bumps**: Always use the version script
2. **Testing**: Test locally before pushing tags
3. **Changelog**: Update CHANGELOG.md before releases
4. **Pre-releases**: Use for major changes (e.g., `1.0.0-rc.1`)
5. **Hotfixes**: Use patch versions for urgent fixes
