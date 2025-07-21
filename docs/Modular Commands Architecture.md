# Modular Commands Architecture

## Overview

deepctl uses a modular architecture where each command is a separate package. This provides better separation of concerns, independent testing, and clear dependency management.

## Structure

```
packages/
├── deepctl-core/          # Base command class and shared functionality
├── deepctl-cmd-login/     # Authentication commands
├── deepctl-cmd-projects/  # Project management
├── deepctl-cmd-transcribe/# Transcription functionality
├── deepctl-cmd-usage/     # Usage statistics
└── deepctl-shared-utils/  # Shared utilities
```

## Command Registration

Commands register via Python entry points in their `pyproject.toml`:

```toml
[project.entry-points."deepctl.commands"]
transcribe = "deepctl_cmd_transcribe.command:TranscribeCommand"
```

The main CLI (`src/deepctl/main.py`) discovers and loads these at startup.

## Creating a New Command

1. Create package structure:

   ```bash
   mkdir -p packages/deepctl-cmd-newcmd/{src/deepctl_cmd_newcmd,tests/unit}
   ```

2. Create `pyproject.toml` with entry point registration

3. Implement command inheriting from `BaseCommand`:

   ```python
   from deepctl_core import BaseCommand

   class NewCommand(BaseCommand):
       name = "newcmd"
       help = "Command description"

       def get_arguments(self):
           return [
               {"names": ["--option"], "help": "Option help", "is_option": True}
           ]

       def execute(self, ctx, **options):
           # Implementation
   ```

4. Add to root `pyproject.toml` dependencies

## Benefits

- **Isolation**: Commands developed/tested independently
- **Clarity**: Explicit dependencies per command
- **Flexibility**: Different commands can use different library versions
- **Maintainability**: Changes isolated to specific commands
