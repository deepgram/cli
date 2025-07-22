# PyPI Publishing Guide

This guide covers the complete process of publishing the deepctl packages to PyPI.

## Pre-Publishing Checklist

### 1. **Version Compatibility**

- [ ] Verify all packages work with Python 3.10-3.12
- [ ] Run local tests: `./scripts/test_local_python_versions.sh`

### 2. **Package Names**

All packages will be published with these names:

- `deepctl` - Main CLI
- `deepctl-core` - Core functionality
- `deepctl-shared-utils` - Shared utilities
- `deepctl-cmd-login` - Login command
- `deepctl-cmd-projects` - Projects command
- `deepctl-cmd-transcribe` - Transcribe command
- `deepctl-cmd-usage` - Usage command
- `deepctl-cmd-debug` - Debug command group
- `deepctl-cmd-debug-audio` - Audio debug
- `deepctl-cmd-debug-browser` - Browser debug
- `deepctl-cmd-debug-network` - Network debug
- `deepctl-cmd-mcp` - MCP server command

### 3. **Build Packages**

```bash
# Clean and build all packages
python scripts/build.py

# Verify all 24 files (12 packages × 2 formats)
ls -la dist/
```

## First-Time Publishing

### Step 1: Create PyPI Account

1. Go to https://pypi.org/account/register/
2. Verify your email
3. Enable 2FA (recommended)

### Step 2: Initial Upload

```bash
# Install twine
pip install twine

# Test with TestPyPI first (optional but recommended)
python scripts/publish.py --test

# Publish to PyPI
python scripts/publish.py
```

You'll be prompted for your PyPI username and password (or API token).

### Step 3: Configure Trusted Publishing

After the first manual upload, set up trusted publishing for future automated releases:

1. **Go to each package on PyPI:**

   - Example: https://pypi.org/manage/project/deepctl/settings/

2. **Add GitHub publisher:**

   - Repository owner: `deepgram`
   - Repository name: `cli`
   - Workflow name: `release.yml`
   - Environment: `pypi` (optional)

3. **Repeat for all 12 packages**

## Automated Releases (After Trusted Publishing)

Once trusted publishing is configured:

```bash
# Update version
python scripts/version.py 0.2.0

# Commit and tag
git add -A
git commit -m "Release v0.2.0"
git tag v0.2.0
git push origin main --tags

# GitHub Actions will automatically:
# 1. Build all packages
# 2. Run tests
# 3. Publish to PyPI
```

## Version Management

### Bump Version

```bash
# All packages share the same version
python scripts/version.py 0.1.1
```

### Version Guidelines

- **PATCH** (0.1.0 → 0.1.1): Bug fixes
- **MINOR** (0.1.0 → 0.2.0): New features, backwards compatible
- **MAJOR** (0.1.0 → 1.0.0): Breaking changes

## Troubleshooting

### "Package already exists"

- You can't overwrite an existing version
- Bump the version and try again

### Authentication Issues

- Use API tokens instead of password
- Create token at: https://pypi.org/manage/account/token/

### Missing Dependencies

- Ensure all workspace dependencies are published
- They must be available on PyPI before the main package

### Testing Failed Uploads

```bash
# Rollback version changes
git checkout -- '**/pyproject.toml'

# Fix issues and try again
```

## Post-Publishing

### Verify Installation

```bash
# Create fresh environment
python -m venv test-install
source test-install/bin/activate  # or test-install\Scripts\activate on Windows

# Install from PyPI
pip install deepctl

# Test it works
deepctl --version
deepctl --help
```

### Update Documentation

- Update README with installation instructions
- Add PyPI badges
- Update any hardcoded version references

### Monitor Usage

- Check download stats: https://pypistats.org/packages/deepctl
- Monitor issues on GitHub
- Set up automated dependency updates

## Security Considerations

1. **Never commit credentials**
2. **Use trusted publishing** for automated releases
3. **Sign releases** with GPG (optional)
4. **Review dependencies** before each release
5. **Test on TestPyPI** before production releases
