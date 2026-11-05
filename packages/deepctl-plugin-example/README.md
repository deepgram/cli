# deepctl-plugin-example

> Part of [deepctl](https://github.com/deepgram/cli) — Official Deepgram CLI

Example plugin for deepctl

## Installation

This package is included with deepctl and does not need to be installed separately.

### Install deepctl

```bash
# Install with pip
pip install deepctl

# Or install with uv
uv tool install deepctl

# Or install with pipx
pipx install deepctl

# Or run without installing
uvx deepctl --help
pipx run deepctl --help
```

## Commands

| Command | Entry Point |
|---------|-------------|
| `deepctl example` | `deepctl_plugin_example.command:ExampleCommand` |

## Naming your entry point

A plugin whose entry point is named like a built-in command, such as `login`,
is skipped before it's imported; give the entry point a different name. The
only exception is a command listed in `plugins.allow_override` in your user
config (see [Plugins](https://github.com/deepgram/cli/blob/main/README.md#plugins)).

## Dependencies

- `click>=8.0.0`
- `rich>=13.0.0`

## License

MIT — see [LICENSE](../../LICENSE)
