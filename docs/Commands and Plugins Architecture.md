# Commands and Plugins Architecture

## Overview

The deepctl CLI supports two types of extensions:

- **Built-in Commands**: Core functionality provided by the deepctl team
- **External Plugins**: Third-party extensions that add custom functionality

Both use Python entry points for discovery, but are loaded from different groups to maintain clear separation.

## Entry Point Groups

### Built-in Commands

Built-in commands use the following entry point groups:

- **Top-level commands**: `deepctl.commands`
- **Subcommands**: `deepctl.subcommands.{parent_command}`

Example configuration in `pyproject.toml`:

```toml
[project.entry-points."deepctl.commands"]
debug = "deepctl_cmd_debug.command:DebugCommand"

[project.entry-points."deepctl.subcommands.debug"]
audio = "deepctl_cmd_debug_audio.command:AudioCommand"
browser = "deepctl_cmd_debug_browser.command:BrowserCommand"
network = "deepctl_cmd_debug_network.command:NetworkCommand"
```

### External Plugins

External plugins use separate entry point groups:

- **Top-level plugins**: `deepctl.plugins`
- **Plugin subcommands**: `deepctl.subplugins.{parent_plugin}`

Example configuration in `pyproject.toml`:

```toml
[project.entry-points."deepctl.plugins"]
example = "deepctl_plugin_example.command:ExampleCommand"

[project.entry-points."deepctl.subplugins.example"]
demo = "deepctl_plugin_example_demo.command:DemoCommand"
```

## Plugin Discovery Process

The `PluginManager` class handles discovery and loading:

1. **Built-in Commands Loading** (`_load_builtin_commands`):

   - Scans `deepctl.commands` entry points
   - Stores references in `self.command_classes`
   - Logs as "Loaded built-in command: {name}"

2. **External Plugins Loading** (`_load_external_plugins`):

   - Scans `deepctl.plugins` entry points
   - Stores references in `self.loaded_plugins`
   - Logs as "Loaded external plugin: {name}"

3. **Subcommand Loading** (`_load_subcommands_for_group`):
   - For each group command, scans both:
     - `deepctl.subcommands.{parent}` for built-in subcommands
     - `deepctl.subplugins.{parent}` for plugin subcommands
   - Adds discovered subcommands to the parent group

## Benefits of Separation

1. **Clear Boundaries**: Easy to distinguish official commands from third-party plugins
2. **Security**: Different loading paths allow for different security policies
3. **Namespacing**: Prevents naming conflicts between built-in and external functionality
4. **Discoverability**: Users can list built-in vs plugin functionality separately
5. **Upgrades**: Built-in commands can be updated without affecting plugins

## Creating a Plugin

To create an external plugin:

1. Create a package that depends on `deepctl-core`
2. Implement a class extending `BaseCommand` or `BaseGroupCommand`
3. Register via the `deepctl.plugins` entry point
4. Distribute as a separate package

Users install plugins with:

```bash
pip install deepctl-plugin-yourname
```

The plugin is automatically discovered on the next run of `deepctl`.

## Future Enhancements

- Plugin verification/signing
- Plugin marketplace/registry
- Plugin dependency management
- Plugin configuration isolation
