# Self Update Feature Demo

This document demonstrates how the self-update feature works in deepctl.

## Installation Detection

The self-update feature automatically detects how deepctl was installed:

```python
from deepctl_core import InstallationDetector

detector = InstallationDetector()
info = detector.detect()

print(f"Installation method: {info.method}")
print(f"Installation path: {info.path}")
print(f"Virtual environment: {info.virtual_env}")
print(f"Editable install: {info.editable}")
```

## Version Checking

Check for updates from PyPI:

```python
from deepctl_core import VersionChecker, Config

config = Config()
checker = VersionChecker(config, current_version="0.1.5")

# Check for updates (async)
import asyncio
version_info = asyncio.run(checker.check_version())

if version_info.update_available:
    print(f"Update available: {version_info.current_version} → {version_info.latest_version}")
else:
    print("You are using the latest version")
```

## Command Line Usage

### Check for Updates

Check if a new version is available:

```bash
# Check only (no installation)
deepctl update --check-only

# Example output:
# Current version: 0.1.5
# Latest version: 0.2.0
# Update available! Run 'deepctl update' to upgrade.
```

### Update deepctl

Update to the latest version:

```bash
# Interactive update
deepctl update

# Example output:
# Checking for updates...
# Update available: 0.1.5 → 0.2.0 (released 2 days ago)
# Run 'deepctl update' to upgrade
#
# Detecting installation method...
# Installation method: pip
# Installation path: /Users/user/.local/lib/python3.11/site-packages
#
# Update command: pip install --upgrade deepctl
#
# Do you want to proceed with the update? [y/N]: y
# Updating deepctl...
# ✓ Successfully updated to version 0.2.0
```

### Skip Confirmation

For automation or scripts:

```bash
deepctl update --yes
```

### Force Update

Force reinstall even if already up to date:

```bash
deepctl update --force
```

## Installation Method Handling

### pip Installation

For standard pip installations:

```bash
# Detected command:
pip install --upgrade deepctl
```

### pipx Installation

For isolated pipx installations:

```bash
# Detected command:
pipx upgrade deepctl
```

### uv Installation

For uv-managed installations:

```bash
# Detected command:
uv pip install --upgrade deepctl
```

### Development Installation

For editable/development installations:

```
Development installation detected.
Please pull the latest changes from the repository:
git pull origin main
```

### System Package Manager

For system-wide installations:

```
Please use your system package manager to update deepctl

# macOS with Homebrew:
brew upgrade deepctl

# Ubuntu/Debian:
sudo apt update && sudo apt upgrade deepctl

# Fedora/RHEL:
sudo dnf upgrade deepctl
```

## Configuration

The self-update feature stores configuration in the deepctl config file:

```yaml
# ~/.config/deepctl/config.yaml
update:
  check_enabled: true
  check_frequency: daily
  last_check: "2024-01-20T10:00:00"
  installation_method: pip
  installation_path: "/Users/user/.local/lib/python3.11/site-packages"
  cached_version_info:
    current_version: "0.1.5"
    latest_version: "0.2.0"
    update_available: true
    release_date: "2024-01-18T15:30:00"
```

## Disable Update Checks

To disable automatic update checks:

```bash
# Via config file
echo "update:\n  check_enabled: false" >> ~/.config/deepctl/config.yaml

# Or via environment variable
export DEEPGRAM_UPDATE_CHECK_ENABLED=false
```

## Error Handling

The self-update feature handles various error scenarios gracefully:

### Network Errors

```
Failed to check for updates: Network error
deepctl will continue to work normally
```

### Permission Errors

```
Update failed: Permission denied
You may need to run with elevated privileges:
sudo pip install --upgrade deepctl
```

### Unknown Installation Method

```
Unable to detect installation method.
Please update deepctl using the same method you used to install it.

Common update commands:
- pip install --upgrade deepctl
- pipx upgrade deepctl
- uv pip install --upgrade deepctl
```

## Implementation Details

### Version Comparison

Uses semantic versioning to compare versions:

- Pre-releases are handled correctly
- Only stable releases are suggested by default

### Caching

Version checks are cached for 24 hours to avoid excessive API calls to PyPI.

### Security

- Only uses HTTPS for PyPI API calls
- Requires user confirmation before updates
- Does not automatically execute commands

## Testing

The update command includes comprehensive unit tests:

```python
# Run tests
uv run pytest packages/deepctl-cmd-update/tests/ -v

# Test scenarios covered:
# - Check-only mode
# - No update available
# - Various installation methods
# - Error handling
# - User confirmation
```
