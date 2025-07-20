# Cross-Platform Development Guide

This guide outlines best practices for maintaining cross-platform compatibility in the deepctl CLI.

## Core Principles

1. **Test on all platforms** - Use CI/CD to test on Linux, Windows, and macOS
2. **Use platform-agnostic libraries** - Prefer libraries that handle OS differences
3. **Never hardcode paths** - Always use proper path handling
4. **Handle environment differences** - Account for OS-specific behaviors
5. **Graceful degradation** - Provide fallbacks when platform features aren't available

## Platform-Specific Considerations

### File System Differences

| Feature          | Windows     | Linux/macOS | Solution                     |
| ---------------- | ----------- | ----------- | ---------------------------- |
| Path separator   | `\`         | `/`         | Use `pathlib.Path`           |
| Case sensitivity | No          | Yes         | Normalize case where needed  |
| Hidden files     | Attributes  | `.` prefix  | Use `platformdirs`           |
| Line endings     | `\r\n`      | `\n`        | Open files in text mode      |
| Max path length  | 260 chars\* | 4096 chars  | Handle long paths gracefully |

\*Windows 10+ with long path support enabled

### Environment Variables

```python
# ✅ Good - Cross-platform environment handling
import os
from typing import Optional

def get_env_var(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with case-insensitive handling for Windows."""
    # Windows env vars are case-insensitive
    if os.name == 'nt':
        for env_key, env_value in os.environ.items():
            if env_key.upper() == key.upper():
                return env_value
    return os.environ.get(key, default)

# ❌ Bad - Only works on Unix systems
api_key = os.environ.get("deepgram_api_key")  # Wrong case on Windows

# ✅ Good - Works on all systems
api_key = get_env_var("DEEPGRAM_API_KEY")
```

### Configuration Directories

```python
# ✅ Good - Cross-platform config paths
import platformdirs
from pathlib import Path

def get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    return Path(platformdirs.user_config_dir("deepgram", "deepgram"))

# Windows: C:\Users\username\AppData\Roaming\deepgram\deepgram
# macOS: ~/Library/Application Support/deepgram
# Linux: ~/.config/deepgram

# ❌ Bad - Unix-only
config_dir = Path.home() / ".deepgram"
```

### Process Management

```python
# ✅ Good - Cross-platform subprocess handling
import subprocess
import sys
from pathlib import Path

def run_command(cmd: list[str], cwd: Path = None) -> subprocess.CompletedProcess:
    """Run command with proper shell handling."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        shell=sys.platform == "win32",  # Shell needed on Windows
        capture_output=True,
        text=True,
        check=True
    )

# ❌ Bad - Unix-specific
subprocess.run(["ls", "-la"], shell=False)  # Fails on Windows
```

### Network and SSL

```python
# ✅ Good - Cross-platform SSL handling
import ssl
import httpx

def create_client() -> httpx.Client:
    """Create HTTP client with proper SSL handling."""
    # Windows may need different certificate handling
    verify = True
    if sys.platform == "win32":
        # Windows might need custom certificate store
        verify = ssl.create_default_context()

    return httpx.Client(verify=verify)
```

## Testing Strategy

### Local Testing

```bash
# Test on multiple Python versions
python3.8 -m pytest
python3.9 -m pytest
python3.10 -m pytest
python3.11 -m pytest
python3.12 -m pytest

# Test with different locale settings
LC_ALL=C python -m pytest
LC_ALL=en_US.UTF-8 python -m pytest
```

### CI/CD Matrix

```yaml
# .github/workflows/test.yml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest, macos-latest]
    python-version: ["3.8", "3.9", "3.10", "3.11", "3.12"]
    include:
      # Test on Apple Silicon
      - os: macos-latest
        python-version: "3.11"
        architecture: arm64
```

### Docker Testing

```dockerfile
# Test on multiple Linux distributions
FROM python:3.11-slim-bullseye
FROM python:3.11-alpine
FROM python:3.11-ubuntu
```

## Common Pitfalls

### 1. Hardcoded Paths

```python
# ❌ Bad
CONFIG_PATH = "~/.deepgram/config.yaml"
TEMP_DIR = "/tmp"

# ✅ Good
CONFIG_PATH = Path.home() / ".deepgram" / "config.yaml"
TEMP_DIR = Path(tempfile.gettempdir())
```

### 2. Case Sensitivity

```python
# ❌ Bad - Assumes case-sensitive filesystem
if filename == "Config.yaml":
    load_config()

# ✅ Good - Handle case differences
if filename.lower() == "config.yaml":
    load_config()
```

### 3. Shell Commands

```python
# ❌ Bad - Unix-specific
os.system("ls -la")

# ✅ Good - Cross-platform
import shutil
files = list(Path(".").iterdir())
```

### 4. Path Joining

```python
# ❌ Bad - Unix-specific
path = base_path + "/" + filename

# ✅ Good - Cross-platform
path = Path(base_path) / filename
```

## Binary Distribution

### PyInstaller Configuration

```python
# build.py - Cross-platform binary builder
import PyInstaller.__main__
import sys

def build_binary():
    """Build platform-specific binary."""
    args = [
        '--onefile',
        '--name=deepctl',
        '--console',
        '--clean',
        f'--distpath=dist/{sys.platform}',
        'src/deepctl/main.py'
    ]

    # Platform-specific options
    if sys.platform == 'win32':
        args.extend(['--icon=assets/icon.ico'])
    elif sys.platform == 'darwin':
        args.extend(['--icon=assets/icon.icns'])

    PyInstaller.__main__.run(args)
```

### GitHub Actions Release

```yaml
# .github/workflows/release.yml
name: Release
on:
  release:
    types: [created]

jobs:
  build:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
            asset_name: deepctl-linux-x86_64
          - os: windows-latest
            asset_name: deepctl-windows-x86_64.exe
          - os: macos-latest
            asset_name: deepctl-macos-x86_64
          - os: macos-latest
            asset_name: deepctl-macos-arm64
            arch: arm64
```

## Performance Considerations

### 1. Lazy Imports

```python
# ✅ Good - Lazy loading for better startup time
def transcribe_audio(file_path: str):
    from deepgram import Deepgram  # Import only when needed
    # ... implementation
```

### 2. Platform-Specific Optimizations

```python
# ✅ Good - Use platform-specific optimizations
if sys.platform == "win32":
    # Windows-specific optimizations
    import winsound
elif sys.platform == "darwin":
    # macOS-specific optimizations
    import objc
```

## Security Considerations

### 1. Keyring Access

```python
# ✅ Good - Cross-platform keyring with fallback
import keyring
from keyring.backends import fail

def store_api_key(key: str) -> bool:
    """Store API key with cross-platform keyring."""
    try:
        keyring.set_password("deepgram", "api_key", key)
        return True
    except Exception:
        # Fallback for headless systems
        return store_in_config_file(key)
```

### 2. File Permissions

```python
# ✅ Good - Cross-platform file permissions
import stat

def create_secure_file(path: Path) -> None:
    """Create file with secure permissions."""
    path.touch()
    if os.name != 'nt':  # Unix systems
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    else:  # Windows
        # Windows file permissions are handled differently
        import win32security
        # Implementation for Windows ACLs
```

## Debugging Platform Issues

### 1. Platform Detection

```python
import platform
import sys

def get_platform_info() -> dict:
    """Get detailed platform information for debugging."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
    }
```

### 2. Path Debugging

```python
def debug_paths():
    """Debug path handling across platforms."""
    print(f"Current working directory: {Path.cwd()}")
    print(f"Home directory: {Path.home()}")
    print(f"Config directory: {platformdirs.user_config_dir('deepgram')}")
    print(f"Cache directory: {platformdirs.user_cache_dir('deepgram')}")
    print(f"Data directory: {platformdirs.user_data_dir('deepgram')}")
    print(f"Temp directory: {Path(tempfile.gettempdir())}")
```

## Resources

- [Python pathlib documentation](https://docs.python.org/3/library/pathlib.html)
- [platformdirs documentation](https://platformdirs.readthedocs.io/)
- [PyInstaller documentation](https://pyinstaller.readthedocs.io/)
- [GitHub Actions matrix builds](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs)
