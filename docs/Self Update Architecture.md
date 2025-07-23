# Self Update Architecture

## Overview

This document outlines the architecture for implementing a self-update feature in deepctl. The feature will:

1. Check for newer versions on PyPI
2. Detect how deepctl was installed
3. Remember installation method
4. Provide appropriate update commands

## Key Components

### 1. Version Checking

#### PyPI API Integration

- Use the PyPI JSON API to check for the latest version
- Compare with current version using semantic versioning
- Cache version check results to avoid excessive API calls

```python
# Example API endpoint
https://pypi.org/pypi/deepctl/json

# Response includes:
- info.version (latest stable version)
- releases (all available versions)
```

#### Version Check Strategy

- Check on startup (configurable frequency)
- Provide explicit `deepctl version --check` command
- Store last check timestamp in config
- Respect user preference to disable checks

### 2. Installation Method Detection

#### Detection Methods

1. **pip/uv Detection**

   - Check `sys.prefix` and package metadata
   - Look for `.dist-info` directory
   - Check if installed in virtual environment

2. **pipx Detection**

   - Check for pipx metadata in installation path
   - Look for pipx-specific directory structure
   - Check environment variable markers

3. **System Package Manager**

   - Check common system paths (/usr/bin, /usr/local/bin)
   - Look for package manager metadata (dpkg, rpm, etc.)

4. **Development Installation**
   - Check for editable installation markers
   - Look for git repository in parent directories

#### Installation Metadata Storage

Store in the configuration system:

```yaml
installation:
  method: "pip" # pip, pipx, uv, system, development, unknown
  path: "/path/to/installation"
  version: "0.1.5"
  installed_at: "2024-01-20T10:00:00Z"
  virtual_env: true
  editable: false
```

### 3. Update Commands

#### Update Strategies by Installation Method

1. **pip**

   ```bash
   pip install --upgrade deepctl
   ```

2. **pipx**

   ```bash
   pipx upgrade deepctl
   ```

3. **uv**

   ```bash
   uv pip install --upgrade deepctl
   ```

4. **System Package Manager**

   - Display message: "Please use your system package manager to update"
   - Provide specific commands for detected OS

5. **Development Installation**
   - Display message: "Development installation detected. Please pull latest changes."

### 4. Implementation Plan

#### Phase 1: Core Infrastructure

1. Create version checking module
2. Implement PyPI API client
3. Add version comparison logic
4. Create installation detector

#### Phase 2: Command Implementation

1. Add `deepctl version` command with `--check` flag
2. Add `deepctl update` command
3. Implement update confirmation workflow

#### Phase 3: Configuration & Preferences

1. Add update preferences to config
2. Implement check frequency control
3. Add option to disable auto-checks

#### Phase 4: User Experience

1. Add progress indicators
2. Implement rollback mechanism
3. Add update notifications

## Detailed Design

### Version Checking Module (`deepctl_core/version_check.py`)

```python
from typing import Optional, Dict, Any
import httpx
from packaging import version
from datetime import datetime, timedelta
from pydantic import BaseModel

class VersionInfo(BaseModel):
    """Version information from PyPI."""
    latest_version: str
    current_version: str
    update_available: bool
    release_date: Optional[datetime]
    release_notes_url: Optional[str]

class VersionChecker:
    """Handles version checking against PyPI."""

    PYPI_API_URL = "https://pypi.org/pypi/deepctl/json"
    CACHE_DURATION = timedelta(hours=24)

    def __init__(self, config: Config):
        self.config = config
        self.current_version = self._get_current_version()

    async def check_version(self, force: bool = False) -> VersionInfo:
        """Check for newer version on PyPI."""
        # Implementation details...

    def should_check(self) -> bool:
        """Determine if version check should run."""
        # Check user preferences and last check time
```

### Installation Detector (`deepctl_core/installation.py`)

```python
import sys
import os
from pathlib import Path
from enum import Enum
from typing import Optional

class InstallMethod(str, Enum):
    """Supported installation methods."""
    PIP = "pip"
    PIPX = "pipx"
    UV = "uv"
    SYSTEM = "system"
    DEVELOPMENT = "development"
    UNKNOWN = "unknown"

class InstallationDetector:
    """Detects how deepctl was installed."""

    def detect(self) -> InstallMethod:
        """Detect installation method."""
        # Check various markers and paths

    def get_update_command(self, method: InstallMethod) -> Optional[str]:
        """Get appropriate update command for installation method."""
        # Return command based on method
```

### Update Command (`deepctl_cmd_update/`)

Create a new command package for updates:

```python
# deepctl_cmd_update/command.py
from deepctl_core import BaseCommand
from deepctl_core.version_check import VersionChecker
from deepctl_core.installation import InstallationDetector

class UpdateCommand(BaseCommand):
    """Check for and install updates."""

    name = "update"
    help = "Check for and install updates"

    def add_arguments(self, parser):
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Only check for updates without installing"
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force update even if up to date"
        )

    async def run(self, args):
        """Run update command."""
        # Check version
        # Detect installation method
        # Prompt for confirmation
        # Execute update
```

### Version Command Enhancement

Enhance existing version command:

```python
@cli.command()
@click.option("--check", is_flag=True, help="Check for updates")
def version(check: bool):
    """Show version information."""
    # Show current version
    # If --check, also check for updates
```

## Configuration Schema Update

Add to `DeepgramConfig`:

```python
class UpdateConfig(BaseModel):
    """Update preferences."""
    check_enabled: bool = True
    check_frequency: str = "daily"  # daily, weekly, never
    last_check: Optional[datetime] = None
    installation_method: Optional[str] = None
    installation_path: Optional[str] = None

class DeepgramConfig(BaseModel):
    # ... existing fields ...
    update: UpdateConfig = Field(default_factory=UpdateConfig)
```

## Security Considerations

1. **HTTPS Only**: Always use HTTPS for PyPI API
2. **Signature Verification**: Consider verifying package signatures
3. **Rollback Support**: Keep previous version for rollback
4. **User Confirmation**: Always require user confirmation for updates
5. **Privilege Escalation**: Handle cases where admin rights needed

## User Experience

### Update Flow

1. **Automatic Check** (on startup, respecting frequency):

   ```
   ╭─────────────────────────────────────╮
   │ Update available: deepctl 0.2.0     │
   │ Run 'deepctl update' to upgrade     │
   ╰─────────────────────────────────────╯
   ```

2. **Manual Check**:

   ```bash
   $ deepctl version --check
   Current version: 0.1.5
   Latest version: 0.2.0
   Update available! Run 'deepctl update' to upgrade.
   ```

3. **Update Command**:

   ```bash
   $ deepctl update
   Current version: 0.1.5
   Latest version: 0.2.0

   Installation method: pip
   Update command: pip install --upgrade deepctl

   Do you want to proceed? [y/N]: y
   Updating deepctl...
   ✓ Successfully updated to version 0.2.0
   ```

### Error Handling

1. **Network Errors**: Graceful fallback, don't block CLI usage
2. **Permission Errors**: Provide clear instructions
3. **Unknown Installation**: Provide manual update instructions

## Testing Strategy

1. **Unit Tests**:

   - Version comparison logic
   - Installation detection
   - API response parsing

2. **Integration Tests**:

   - Mock PyPI API responses
   - Test various installation scenarios
   - Test update workflows

3. **Manual Testing**:
   - Test on different platforms
   - Test with different installation methods
   - Test error scenarios

## Implementation Timeline

1. **Week 1**: Core version checking infrastructure
2. **Week 2**: Installation detection logic
3. **Week 3**: Update command implementation
4. **Week 4**: Testing and documentation

## Future Enhancements

1. **Plugin Updates**: Check and update plugins
2. **Batch Updates**: Update all deepctl packages together
3. **Beta Channel**: Support pre-release versions
4. **Offline Mode**: Work without internet connection
5. **Update History**: Track update history
6. **Automatic Updates**: Optional automatic updates (with user consent)

## Conclusion

The self-update feature is highly feasible and will significantly improve the user experience. By detecting installation methods and providing appropriate update commands, we can support various deployment scenarios while maintaining security and reliability.
