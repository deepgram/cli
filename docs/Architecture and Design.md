# Architecture and Design

## Overview

The Deepgram CLI (`deepctl`) is a Python-based command-line interface designed to help customers maximize their use of Deepgram's speech recognition services. It emphasizes modularity, extensibility, and ease of distribution.

## Core Principles

1. **Cross-platform Compatibility**: MUST work identically on Linux, Windows, macOS (Intel), and macOS (Silicon)
2. **Modularity**: Each feature is implemented as a separate module/plugin
3. **Extensibility**: Plugin system allows for easy addition of new commands
4. **User Experience**: Simple installation and usage patterns
5. **Community Integration**: Authentication through Deepgram's community site

## Architecture Overview

```
deepctl/
├── src/
│   ├── deepgram_cli/
│   │   ├── __init__.py
│   │   ├── main.py              # Entry point
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py        # Configuration management
│   │   │   ├── auth.py          # Authentication system
│   │   │   ├── client.py        # Deepgram SDK wrapper
│   │   │   └── plugin_manager.py # Plugin system
│   │   ├── commands/            # Core commands
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # Base command class
│   │   │   ├── login.py         # Authentication commands
│   │   │   ├── transcribe.py    # Transcription commands
│   │   │   ├── projects.py      # Project management
│   │   │   └── usage.py         # Usage statistics
│   │   ├── plugins/             # Plugin directory
│   │   │   ├── __init__.py
│   │   │   └── examples/        # Example plugins
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── output.py        # Output formatting
│   │       └── validation.py    # Input validation
├── tests/
├── docs/
├── scripts/                     # Build and distribution scripts
├── pyproject.toml              # Modern Python packaging (all dependencies)
└── README.md
```

## Technology Stack

### Core Framework
- **Click**: For CLI interface (chosen for its decorators, auto-completion, and plugin support)
- **Deepgram SDK**: Official Python SDK for API integration
- **Pydantic**: For configuration validation and data models
- **Rich**: For beautiful terminal output and progress bars

### Authentication
- **httpx**: For HTTP requests to community site
- **JWT**: For token management
- **Keyring**: For secure credential storage

### Distribution
- **uv**: Modern Python package manager (recommended)
- **setuptools**: For Python packaging (fallback)
- **pipx/uvx**: For isolated installation (npx-like experience)
- **PyInstaller**: For binary creation
- **GitHub Actions**: For CI/CD and multi-platform builds

## Plugin System

### Plugin Architecture
Plugins are Python modules that extend the CLI with new commands. They follow a standard interface:

```python
from deepgram_cli.commands.base import BaseCommand

class MyPlugin(BaseCommand):
    name = "my-command"
    help = "Description of my command"
    
    def add_arguments(self, parser):
        # Add command-specific arguments
        pass
    
    def handle(self, args):
        # Command implementation
        pass
```

### Plugin Discovery
- Plugins are discovered through entry points in `pyproject.toml`
- Third-party plugins can be installed as separate packages
- Built-in plugins are in the `commands/` directory

## Authentication System

### Community Site Integration
- OAuth2 flow with Deepgram's community site
- Secure token storage using system keyring
- Automatic token refresh
- Support for multiple profiles/accounts

### API Key Management
- Direct API key input for advanced users
- Project-specific API keys
- Environment variable support

## Configuration Management

### Configuration Hierarchy
1. Command-line arguments (highest priority)
2. Environment variables
3. User configuration file (`~/.deepgram/config.yaml`)
4. Project configuration file (`./deepgram.yaml`)
5. Default values (lowest priority)

### Configuration Schema
```yaml
# ~/.deepgram/config.yaml
default_profile: "production"
profiles:
  production:
    api_key: "encrypted_key"
    project_id: "project_uuid"
  development:
    api_key: "encrypted_key"
    project_id: "project_uuid"
output:
  format: "json"  # json, yaml, table, csv
  color: true
plugins:
  enabled:
    - transcribe
    - projects
    - usage
```

## Distribution Strategy

### PyPI Distribution (npx-like experience)
- Primary distribution through PyPI
- Users can run: `uvx deepctl [command]` (recommended) or `pipx run deepctl [command]`
- Installable via: `uv tool install deepctl` (recommended) or `pipx install deepctl`
- Alternative commands: `deepgram` and `dg` are available as aliases

### Binary Distribution
- **macOS**: 
  - Homebrew formula (universal binary for Intel/Silicon)
  - Direct download from GitHub releases
- **Linux**: 
  - APT/YUM repositories (x86_64, arm64)
  - Snap packages (universal)
  - AppImage (portable)
- **Windows**: 
  - Chocolatey package
  - Scoop package
  - MSI installer
  - Portable executable
- **Cross-platform**: GitHub releases with pre-built binaries for all architectures

### Container Distribution
- Docker image for containerized environments
- Multi-architecture support (amd64, arm64)

## Cross-Platform Compatibility Requirements

### File System Handling
- **Path Separators**: Use `pathlib.Path` exclusively, never hardcode `/` or `\`
- **Home Directory**: Use `Path.home()` instead of `~` or environment variables
- **Temp Directory**: Use `tempfile.gettempdir()` for temporary files
- **Config Directory**: Use `platformdirs` library for OS-appropriate config locations
- **File Permissions**: Handle Windows vs Unix permission models gracefully

### Environment Variables
- **Case Sensitivity**: Windows env vars are case-insensitive, Unix are case-sensitive
- **PATH Handling**: Use `os.pathsep` for PATH separator (`;` on Windows, `:` on Unix)
- **User Variables**: Handle different user environment setups across OSes

### System Integration
- **Keyring Access**: Use `keyring` library with fallback for headless systems
- **Process Management**: Use `subprocess` with proper shell handling
- **Network/SSL**: Handle different certificate stores and proxy configurations
- **Terminal**: Use `rich` library for consistent color/formatting support

### Binary Distribution
- **Architecture Support**: 
  - Windows: x86_64
  - Linux: x86_64, arm64
  - macOS: x86_64 (Intel), arm64 (Apple Silicon)
- **Package Managers**: Support native package managers per platform
- **Dependencies**: Minimize native dependencies, prefer pure Python

### Testing Strategy
- **CI/CD**: Test on all target platforms (GitHub Actions matrix)
- **Local Testing**: Docker containers for Linux variants
- **Real Hardware**: Test on actual macOS Silicon and Intel hardware
- **Edge Cases**: Test with different Python versions and configurations

### Code Standards
```python
# ✅ Good - Cross-platform
from pathlib import Path
config_path = Path.home() / ".deepgram" / "config.yaml"

# ❌ Bad - Unix-specific
config_path = "~/.deepgram/config.yaml"

# ✅ Good - Cross-platform
import tempfile
temp_dir = Path(tempfile.gettempdir())

# ❌ Bad - Unix-specific
temp_dir = "/tmp"
```

## Security Considerations

1. **Credential Storage**: Use system keyring for secure storage
2. **API Key Handling**: Never log or expose API keys
3. **Network Security**: TLS for all communications
4. **Input Validation**: Sanitize all user inputs
5. **Permission Model**: Minimal required permissions

## Performance Considerations

1. **Lazy Loading**: Load plugins only when needed
2. **Caching**: Cache API responses where appropriate
3. **Streaming**: Support streaming for large files
4. **Parallel Processing**: Batch operations where possible

## Testing Strategy

1. **Unit Tests**: Test individual components
2. **Integration Tests**: Test CLI commands end-to-end
3. **Plugin Tests**: Test plugin system
4. **Distribution Tests**: Test packaging and installation
5. **Security Tests**: Test authentication and authorization

## Maintenance and Updates

1. **Semantic Versioning**: Clear versioning strategy
2. **Backward Compatibility**: Maintain API stability
3. **Deprecation Policy**: Clear deprecation warnings
4. **Documentation**: Keep docs updated with changes
5. **Community Feedback**: Regular user feedback collection

## Future Considerations

1. **Web UI**: Optional web interface for complex operations
2. **IDE Integration**: Plugins for popular IDEs
3. **Mobile Support**: Companion mobile app
4. **Analytics**: Usage analytics for improvement
5. **Localization**: Multi-language support 