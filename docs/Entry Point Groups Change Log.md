# Entry Point Groups Change Log

## Update: Separate Entry Point Groups for Commands and Plugins

### Change Date

Current session

### Summary

Updated the plugin manager to use separate entry point groups for built-in commands and external plugins, providing clearer separation and better organization.

### Entry Point Groups

#### Built-in Commands

- **Top-level**: `deepctl.commands`
- **Subcommands**: `deepctl.subcommands.{parent}`

#### External Plugins

- **Top-level**: `deepctl.plugins`
- **Subcommands**: `deepctl.subplugins.{parent}`

### Files Modified

1. **packages/deepctl-core/src/deepctl_core/plugin_manager.py**

   - Updated `_load_builtin_commands()` to load from `deepctl.commands`
   - Updated `_load_external_plugins()` to load from `deepctl.plugins`
   - Updated `_load_subcommands_for_group()` to check both entry point groups
   - Different logging messages for built-in vs external

2. **packages/deepctl-plugin-example/pyproject.toml**

   - Changed entry point from `deepctl.commands` to `deepctl.plugins`

3. **packages/deepctl-plugin-example/README.md**

   - Updated documentation to reflect new entry point

4. **packages/deepctl-core/tests/unit/test_plugin_manager.py**

   - Updated test to expect two calls when loading subcommands

5. **Documentation Updates**
   - Created new "Commands and Plugins Architecture.md"
   - Updated "Architecture and Design.md"
   - Updated "Home.md" with new doc link

### Benefits

- **Clear distinction** between official commands and third-party plugins
- **Better security** - different loading paths allow different security policies
- **Namespace separation** - prevents naming conflicts
- **Easier discoverability** - can list built-in vs external separately
- **Independent updates** - core commands can be updated without affecting plugins

### Migration Notes

External plugins should update their `pyproject.toml` from:

```toml
[project.entry-points."deepctl.commands"]
```

to:

```toml
[project.entry-points."deepctl.plugins"]
```

For plugin subcommands, use:

```toml
[project.entry-points."deepctl.subplugins.{parent}"]
```
