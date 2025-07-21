# Nested Command Architecture

This document explains the nested command architecture in `deepctl` and provides guidance on creating new nested commands.

## Overview

The nested command architecture allows commands to be organized in a hierarchical structure, where commands can contain subcommands. This is particularly useful for grouping related functionality together, such as all debug utilities under a single `debug` command.

## Architecture Components

### BaseGroupCommand

The `BaseGroupCommand` class extends `BaseCommand` to provide group functionality:

```python
from deepctl_core import BaseGroupCommand

class DebugCommand(BaseGroupCommand):
    name = "debug"
    help = "Debug utilities for troubleshooting"

    def handle_group(self, config, auth_manager, client, **kwargs):
        # Optional: logic to run when group is invoked without subcommand
        # By default, shows help
        pass
```

### Key Features

1. **Automatic Help Generation**: Groups automatically show help when invoked without a subcommand
2. **Subcommand Discovery**: Subcommands are discovered through entry points
3. **Context Inheritance**: Subcommands inherit configuration and authentication from the parent
4. **Hyphenated Command Support**: Commands with underscores in entry points are converted to hyphens

## Creating a New Nested Command Structure

### Step 1: Create the Parent Group Command

Create a new package for your group command:

```bash
packages/deepctl-cmd-mygroup/
├── pyproject.toml
├── README.md
├── src/
│   └── deepctl_cmd_mygroup/
│       ├── __init__.py
│       ├── command.py
│       └── models.py
└── tests/
    └── unit/
```

**command.py:**

```python
from deepctl_core import BaseGroupCommand, Config, AuthManager, DeepgramClient
from rich.console import Console

console = Console()

class MyGroupCommand(BaseGroupCommand):
    """Group command for related functionality."""

    name = "mygroup"
    help = "My group of related commands"
    short_help = "My group commands"

    # Optional: Set to True if you want to handle group invocation without subcommand
    # invoke_without_command = True

    def handle_group(self, config: Config, auth_manager: AuthManager,
                    client: DeepgramClient, **kwargs) -> Any:
        """Handle group-specific logic.

        This is called when the group is invoked, regardless of subcommands.
        Override this to add group-level functionality.
        """
        # Optional: Add any group-level logic here
        pass
```

**pyproject.toml:**

```toml
[project]
name = "deepctl-cmd-mygroup"
version = "0.1.0"
description = "My group command for deepctl"

dependencies = [
    "deepctl-core",
]

[project.entry-points."deepctl.commands"]
mygroup = "deepctl_cmd_mygroup.command:MyGroupCommand"
```

### Step 2: Create Subcommands

Create packages for each subcommand:

```bash
packages/deepctl-cmd-mygroup-subcommand/
├── pyproject.toml
├── README.md
├── src/
│   └── deepctl_cmd_mygroup_subcommand/
│       ├── __init__.py
│       ├── command.py
│       └── models.py
└── tests/
    └── unit/
```

**command.py:**

```python
from deepctl_core import BaseCommand, Config, AuthManager, DeepgramClient
from rich.console import Console
from typing import Any, Dict, List

console = Console()

class MySubCommand(BaseCommand):
    """Subcommand implementation."""

    name = "subcommand"
    help = "Perform a specific action"
    short_help = "Specific action"

    def get_arguments(self) -> List[Dict[str, Any]]:
        """Define command arguments and options."""
        return [
            {
                "names": ["--verbose", "-v"],
                "help": "Enable verbose output",
                "is_flag": True,
                "is_option": True,
            },
            {
                "name": "input",
                "help": "Input parameter",
                "type": str,
                "required": True,
                "is_option": False,
            }
        ]

    def handle(self, config: Config, auth_manager: AuthManager,
              client: DeepgramClient, **kwargs) -> Any:
        """Handle the subcommand execution."""
        verbose = kwargs.get("verbose", False)
        input_value = kwargs.get("input")

        if verbose:
            console.print(f"[dim]Processing {input_value}...[/dim]")

        # Implement your command logic here
        result = {"processed": input_value}

        console.print(f"[green]✓[/green] Processed successfully")
        return result
```

**pyproject.toml:**

```toml
[project]
name = "deepctl-cmd-mygroup-subcommand"
version = "0.1.0"
description = "Subcommand for mygroup"

dependencies = [
    "deepctl-core",
]

[project.entry-points."deepctl.subcommands.mygroup"]
subcommand = "deepctl_cmd_mygroup_subcommand.command:MySubCommand"
```

Note the entry point group: `deepctl.subcommands.mygroup` - this tells the plugin manager that this is a subcommand of `mygroup`.

### Step 3: Install and Test

1. Install both packages in development mode:

```bash
cd packages/deepctl-cmd-mygroup
pip install -e .

cd packages/deepctl-cmd-mygroup-subcommand
pip install -e .
```

2. Test the command structure:

```bash
# Show group help
deepctl mygroup --help

# Execute subcommand
deepctl mygroup subcommand "test input" --verbose
```

## Entry Point Patterns

The plugin manager uses specific entry point patterns for command discovery:

- **Top-level commands**: `deepctl.commands`
- **Subcommands**: `deepctl.subcommands.{parent-name}`

For deeply nested commands (if needed):

- `deepctl.subcommands.parent.child`

## Naming Conventions

1. **Package Names**: Use hyphens in package names (e.g., `deepctl-cmd-debug-audio`)
2. **Entry Point Names**: Can use underscores or hyphens (will be normalized)
3. **Command Names**: Use hyphens in the command's `name` attribute for consistency
4. **Python Identifiers**: Use underscores for class names and functions

## Advanced Features

### Invoke Without Command

If you want your group to perform an action when invoked without a subcommand:

```python
class MyGroupCommand(BaseGroupCommand):
    name = "mygroup"
    help = "My group command"
    invoke_without_command = True  # Enable group invocation

    def handle_group(self, config, auth_manager, client, **kwargs):
        # This will run when user types: deepctl mygroup
        console.print("Running group action...")
        return {"group_action": "completed"}
```

### Group-Level Options

Groups can have their own options that are available to all subcommands:

```python
class MyGroupCommand(BaseGroupCommand):
    name = "mygroup"
    help = "My group command"

    def get_arguments(self) -> List[Dict[str, Any]]:
        return [
            {
                "names": ["--format", "-f"],
                "help": "Output format",
                "type": str,
                "default": "json",
                "is_option": True,
            }
        ]

    def handle_group(self, config, auth_manager, client, **kwargs):
        # Format will be available in kwargs for all subcommands
        pass
```

### Authentication Requirements

Groups and subcommands can specify authentication requirements independently:

```python
class SecureGroupCommand(BaseGroupCommand):
    name = "secure"
    help = "Secure operations"
    requires_auth = True  # All subcommands will require auth
    requires_project = True  # All subcommands will require project selection
```

## Testing Nested Commands

### Unit Tests

Test the group command:

```python
def test_group_command():
    group = MyGroupCommand()
    assert group.is_group is True
    assert group.name == "mygroup"

    # Test subcommand registration
    group.add_subcommand("test", TestSubCommand)
    assert "test" in group.get_subcommands()
```

### Integration Tests

Test the full command hierarchy:

```python
def test_nested_command_execution(runner):
    result = runner.invoke(cli, ["mygroup", "subcommand", "input", "--verbose"])
    assert result.exit_code == 0
    assert "Processed successfully" in result.output
```

## Best Practices

1. **Group Related Commands**: Only create groups when you have multiple related commands
2. **Consistent Naming**: Use consistent naming patterns across your command hierarchy
3. **Clear Help Text**: Provide clear, concise help text for both groups and subcommands
4. **Handle Errors Gracefully**: Implement proper error handling in subcommands
5. **Document Dependencies**: Clearly document any dependencies between commands
6. **Test Thoroughly**: Write both unit and integration tests for your command structure

## Example: Debug Command Structure

The `debug` command demonstrates the nested architecture:

```
deepctl debug             # Shows help for debug group
deepctl debug audio       # Run audio debugging
deepctl debug browser     # Run browser debugging
deepctl debug network     # Run network debugging
```

Each subcommand is a separate package with its own entry point, making the system modular and maintainable.
