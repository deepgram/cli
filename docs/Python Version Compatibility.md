# Python Version Compatibility

## Overview

deepctl requires Python 3.10 or higher. All packages are tested and supported on Python 3.10, 3.11, and 3.12.

## Supported Versions

- **Python 3.10** ✅
- **Python 3.11** ✅
- **Python 3.12** ✅
- **Python 3.13** 🔜 (coming soon)

## Why Python 3.10+?

We chose Python 3.10 as the minimum version to:

- Use modern Python features and type annotations
- Ensure compatibility with all dependencies (including MCP which requires 3.10+)
- Provide consistent behavior across all deepctl commands
- Reduce maintenance burden while supporting recent Python versions

## Testing Python Compatibility

### 1. GitHub Actions (Automated)

The `.github/workflows/test.yml` workflow tests against:

- Python 3.10, 3.11, 3.12
- Ubuntu, macOS, and Windows
- All packages including MCP

### 2. Local Testing with Tox

**Before PyPI Publication:**

```bash
# Tox is configured to use local packages
uv run tox

# Test specific version
uv run tox -e py310
```

**After PyPI Publication:**

```bash
# Install tox
pip install tox

# Test all Python versions
tox

# Test specific version
tox -e py310

# Run linting
tox -e lint
```

### 3. Manual Testing with pyenv

If you need to test multiple Python versions locally without tox:

```bash
# Install Python versions
pyenv install 3.10.13
pyenv install 3.11.7
pyenv install 3.12.1

# Test each version
pyenv shell 3.10.13
python -m venv .venv-310
source .venv-310/bin/activate  # or .venv-310\Scripts\activate on Windows
pip install -e .
pytest --all
```

## Best Practices

1. **Test Regularly**: Run the test matrix before releases
2. **Update Classifiers**: Only list Python versions you actually test
3. **Document Requirements**: Be clear about which features need which Python version
4. **Use Type Hints**: But ensure they're compatible with your minimum Python version
5. **Check Dependencies**: Ensure all dependencies support your minimum Python version

## Dependency Compatibility Check

Before claiming support for a Python version, verify all dependencies:

```python
# Check if all deps support Python 3.10
import subprocess
import json

deps = ["click", "deepgram-sdk", "pydantic", "rich", "httpx", "mcp"]
for dep in deps:
    result = subprocess.run(
        ["pip", "index", "versions", dep, "--json"],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)
    print(f"{dep}: {data.get('requires_python', 'Unknown')}")
```

## Current Test Results

As of the last test run:

- ✅ Python 3.10-3.12: All tests pass
