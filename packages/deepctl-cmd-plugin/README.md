# deepctl-cmd-plugin

Plugin management command for deepctl CLI.

## Description

This command provides plugin management functionality for deepctl, allowing users to:

- Install plugins into the same environment as deepctl
- List installed plugins
- Update plugins
- Remove plugins

This solves the environment isolation issue that occurs when deepctl is installed via pipx, uv, brew, or other package managers that create isolated environments.

## Commands

- `deepctl plugin install <package>` - Install a plugin
- `deepctl plugin list` - List installed plugins
- `deepctl plugin update <package>` - Update a plugin
- `deepctl plugin remove <package>` - Remove a plugin

## Development

This is a built-in command that is part of the deepctl core distribution.
