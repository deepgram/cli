# Plugin Management Command Architecture

## Overview

The `deepctl plugin` command provides built-in plugin management capabilities, solving the environment isolation issue that occurs when deepctl is installed via package managers like pipx, uv, brew, or chocolatey.

## Problem Statement

Different installation methods create isolated environments:

- **pipx**: Creates an isolated virtual environment per application
- **uv tool**: Similar isolation to pipx
- **brew/chocolatey**: System-level installations with restricted modification
- **pip**: Direct installation into Python environment (user or system)

This isolation makes plugin management complex, as plugins need to be installed in the same environment as deepctl to be discovered via entry points.

## Solution

The `deepctl plugin` command detects how deepctl was installed and manages plugins accordingly:

1. **For pip installations**: Directly installs plugins using subprocess calls to pip
2. **For pipx/uv installations**: Uses the same Python executable to ensure plugins are installed in the correct environment
3. **For system installations**: Blocks plugin operations with clear error messages

## Implementation Details

### Key Components

1. **InstallationDetector** (reused from update command)

   - Detects installation method (pip, pipx, uv, system, development)
   - Provides installation-specific information

2. **PluginCommand** (BaseGroupCommand)

   - Provides subcommands: install, list, update, remove
   - Uses subprocess to execute pip commands in the correct environment

3. **Plugin Discovery**
   - Scans entry points in groups:
     - `deepctl.plugins` - External plugins
     - `deepctl.commands` - Built-in command packages

### Command Structure

```
deepctl plugin
├── install <package> [--version] [--upgrade] [--pre] [--force-reinstall] [--git <url>] [--editable]
├── list [--verbose]
├── update <package> [--pre]
└── remove <package> [--yes]
```

### Installation Flow

1. User runs `deepctl plugin install <package>`
2. Command detects installation method
3. If pip/pipx/uv: Builds pip command with appropriate Python executable
4. If system: Returns error with helpful message
5. Executes pip install in subprocess
6. Verifies installation by checking entry points

### Security Considerations

- No arbitrary code execution
- Uses subprocess with specific pip commands only
- Validates package names before installation
- Shows clear error messages from pip

### Error Handling

1. **System Installations**: Clear message that plugins cannot be installed
2. **Missing Packages**: Helpful error when trying to update/remove non-existent plugins
3. **pip Failures**: Passes through pip error messages for debugging

## Benefits

1. **Unified Interface**: Single command for all plugin operations
2. **Environment Awareness**: Automatically handles different installation methods
3. **User-Friendly**: No need to understand virtual environments or pip inject
4. **Consistent**: Works the same way regardless of how deepctl was installed (when possible)

## Limitations

- Cannot install plugins for system-managed installations (brew, chocolatey)
- Requires pip to be available in the environment
- Plugin discovery relies on Python entry points

## Future Enhancements

1. Plugin marketplace/registry integration
2. Plugin dependency resolution
3. Plugin compatibility checking
4. Automatic plugin updates
5. Plugin configuration management
