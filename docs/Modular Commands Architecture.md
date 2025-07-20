# Modular Commands Architecture

## Overview

The deepctl commands have been refactored into individual packages within the workspace. This modular architecture provides:

- Better separation of concerns
- Independent testing and development
- Easier maintenance and updates
- Clear dependency management

## Structure

```
cli/
├── src/deepctl/
│   └── main.py                    # Main entry point that discovers commands
├── packages/
│   ├── deepctl-cmd-login/         # Login command
│   ├── deepctl-cmd-projects/      # Projects command
│   ├── deepctl-cmd-transcribe/    # Transcribe command
│   └── deepctl-cmd-usage/         # Usage command
```

## Command Registration

Commands are registered via Python entry points in each package's `pyproject.toml`:

```toml
[project.entry-points."deepctl.commands"]
login = "deepctl_cmd_login.command:LoginCommand"
```

## Package Structure

Each command package follows this structure:

```
deepctl-cmd-<name>/
├── pyproject.toml
├── src/
│   └── deepctl_cmd_<name>/
│       ├── __init__.py
│       ├── command.py      # Command implementation
│       └── models.py       # Pydantic models specific to this command
└── tests/
    └── unit/
        └── test_<name>.py
```

## Dependencies

Each command package depends on:

- `deepctl-core` - Base command class and shared functionality
- Command-specific dependencies (e.g., `pandas` for data processing)

## Creating a New Command

1. Create the package structure:

```bash
mkdir -p packages/deepctl-cmd-newcmd/{src/deepctl_cmd_newcmd,tests/unit}
```

2. Create `pyproject.toml`:

```toml
[project]
name = "deepctl-cmd-newcmd"
version = "0.1.0"
description = "New command for deepctl"
dependencies = [
    "deepctl-core",
    # Add command-specific dependencies
]

[project.scripts]
# Internal commands not meant to be called directly

[project.entry-points."deepctl.commands"]
newcmd = "deepctl_cmd_newcmd.command:NewCommand"
```

3. Implement the command:

```python
# src/deepctl_cmd_newcmd/command.py
from deepctl_core import BaseCommand

class NewCommand(BaseCommand):
    name = "newcmd"
    help = "Description of the new command"

    def add_arguments(self, parser):
        """Add command-specific arguments."""
        parser.add_argument("--option", help="Command option")

    def execute(self, **options):
        """Execute the command."""
        # Command implementation
```

4. Add to main `pyproject.toml` dependencies:

```toml
dependencies = [
    # ... existing dependencies
    "deepctl-cmd-newcmd",
]
```

## Testing

Each command has its own test suite:

```bash
# Test individual command
uv run pytest packages/deepctl-cmd-login/tests/

# Test all commands
uv run pytest
```

## Benefits

1. **Isolation**: Commands can be developed and tested independently
2. **Clarity**: Each command's dependencies are explicit
3. **Flexibility**: Commands can have different dependency versions if needed
4. **Maintainability**: Changes to one command don't affect others

## Migration from Monolithic Structure

1. **Imports**: Update all imports from `deepctl.commands.X` to `deepctl_cmd_X`
2. **Entry Points**: Commands now register via entry points instead of direct imports
3. **Dependencies**: Command-specific dependencies moved to individual packages
