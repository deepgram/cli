# @deepgram/cli

The official [Deepgram CLI](https://github.com/deepgram/cli): speech-to-text,
text-to-speech, voice agents, audio intelligence and project management from
your terminal.

```bash
npx @deepgram/cli --help
```

or install it globally:

```bash
npm install -g @deepgram/cli
dg login
dg listen recording.wav
dg speak "Hello from Deepgram" -o hello.wav
```

The package installs two commands, `dg` and `deepgram`. They are the same.

## How it works

The Deepgram CLI is written in Python and published to PyPI as
[`deepctl`](https://pypi.org/project/deepctl/). This npm package is a small
launcher with no dependencies and no install scripts. It runs `deepctl` at the
same version as the npm package:

1. If an earlier run already set up a private Python environment for this
   version, it runs that. This is fast and works offline.
2. Otherwise, if [uv](https://docs.astral.sh/uv/) is installed, it runs
   `uv tool run --from deepctl==<version> deepctl`. uv brings its own Python.
3. Otherwise it finds Python 3.10 or newer and builds a private venv under
   your cache directory (`~/.cache/deepgram-cli`, `~/Library/Caches/deepgram-cli`
   or `%LOCALAPPDATA%\deepgram-cli`). The first run takes about a minute.

Launcher messages go to stderr, so stdout carries only the CLI's own output
(`dg ... -o json`, `dg mcp`).

## Prerequisites

One of:

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended):
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, or on Windows
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- Python 3.10 or newer with `venv`. On Debian and Ubuntu that means
  `sudo apt install python3-venv`.

Node.js 18 or newer runs the launcher.

## Already installed the CLI another way?

If you installed the CLI with Homebrew, pip, pipx, uv or the install script,
you don't need this package. Use one install method: they all provide a
`dg` command, and two of them on the same machine fight over it. To switch to
npm, uninstall the other first (for example `brew uninstall deepgram` or
`pip uninstall deepctl`), then `npm install -g @deepgram/cli`.

Update the npm version with `npm install -g @deepgram/cli@latest`, or run
`npx @deepgram/cli@latest`.

## Environment variables

| Variable | Effect |
| --- | --- |
| `DEEPCTL_NPM_RUNNER=uvx\|venv\|path` | Force one strategy. `path` runs a `deepctl` you installed yourself from your `PATH`. |
| `DEEPCTL_NPM_PYPI_SPEC` | Replace the requirement installed from PyPI (default `deepctl==<version>`). Meant for testing. |
| `XDG_CACHE_HOME` | Cache directory base on Linux. |

## Documentation

- CLI reference and source: <https://github.com/deepgram/cli>
- Deepgram docs: <https://developers.deepgram.com>
