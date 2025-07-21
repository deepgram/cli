# Modular Commands Architecture

## Overview

deepctl uses a modular architecture where each command is a separate package. This provides better separation of concerns, independent testing, and clear dependency management.

## Structure

```
packages/
├── deepctl-core/                # Base command class and shared functionality
├── deepctl-cmd-login/           # Authentication commands
├── deepctl-cmd-projects/        # Project management
├── deepctl-cmd-transcribe/      # Transcription functionality
├── deepctl-cmd-usage/           # Usage statistics
├── deepctl-cmd-debug/           # Debug group command
├── deepctl-cmd-debug-audio/     # Debug audio subcommand
├── deepctl-cmd-debug-browser/   # Debug browser subcommand
├── deepctl-cmd-debug-network/   # Debug network subcommand
└── deepctl-shared-utils/        # Shared utilities
```

## Command Registration

Commands register via Python entry points in their `pyproject.toml`:

### Top-Level Commands

```toml
[project.entry-points."deepctl.commands"]
transcribe = "deepctl_cmd_transcribe.command:TranscribeCommand"
```

### Group Commands

Group commands that contain subcommands use the same entry point:

```toml
[project.entry-points."deepctl.commands"]
debug = "deepctl_cmd_debug.command:DebugCommand"
```

### Subcommands

Subcommands register under their parent's namespace:

```toml
[project.entry-points."deepctl.subcommands.debug"]
audio = "deepctl_cmd_debug_audio.command:AudioDebugCommand"
browser = "deepctl_cmd_debug_browser.command:BrowserDebugCommand"
network = "deepctl_cmd_debug_network.command:NetworkDebugCommand"
```

The pattern is: `deepctl.subcommands.{parent-name}`

## Plugin Discovery

The main CLI (`src/deepctl/main.py`) discovers and loads these at startup:

1. **Top-level commands** are discovered from `deepctl.commands` entry points
2. **Group commands** are identified by their `is_group` attribute
3. **Subcommands** are discovered from `deepctl.subcommands.{parent}` entry points
4. **Hyphenated names** are normalized (underscores in entry points work)

## Creating a New Command

### Simple Command

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

       def handle(self, config, auth_manager, client, **kwargs):
           # Implementation
   ```

### Group Command with Subcommands

1. Create the group command package:

   ```bash
   mkdir -p packages/deepctl-cmd-mygroup/{src/deepctl_cmd_mygroup,tests/unit}
   ```

2. Implement group using `BaseGroupCommand`:

   ```python
   from deepctl_core import BaseGroupCommand

   class MyGroupCommand(BaseGroupCommand):
       name = "mygroup"
       help = "Group of related commands"
   ```

3. Create subcommand packages and register them:

   ```toml
   [project.entry-points."deepctl.subcommands.mygroup"]
   subcommand = "deepctl_cmd_mygroup_subcommand.command:SubCommand"
   ```

4. Add all packages to root `pyproject.toml` dependencies

## Benefits

- **Isolation**: Commands developed/tested independently
- **Clarity**: Explicit dependencies per command
- **Flexibility**: Different commands can use different library versions
- **Maintainability**: Changes isolated to specific commands
- **Modularity**: Subcommands can be added without modifying parent
- **Discoverability**: Plugin system automatically finds and loads commands

## Entry Point Naming

- Entry point names can use underscores or hyphens
- Command `name` attribute should use hyphens for consistency
- Package names should use hyphens (e.g., `deepctl-cmd-debug-audio`)
- Python module names use underscores (e.g., `deepctl_cmd_debug_audio`)
