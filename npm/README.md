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
2. Otherwise, if [uv](https://docs.astral.sh/uv/) is installed, uv builds that
   environment. uv brings its own Python, and setup takes a few seconds.
3. Otherwise it finds Python 3.10 to 3.14 and builds the environment with
   `venv` and `pip`. The first run takes about a minute.

Either way, the environment lives under your cache directory
(`~/.cache/deepgram-cli` or `~/Library/Caches/deepgram-cli`), and setup
installs exactly the packages listed in the npm package's
`requirements-<version>.txt`, each checked against its sha256 hash, and only
as prebuilt wheels (`--require-hashes --no-deps --only-binary :all:`). A
download that doesn't match the release is refused and nothing runs. Setup
runs in a directory the launcher owns, so running `dg` inside a cloned
repository can't change what gets installed: uv ignores any `uv.toml` or
`pyproject.toml` there.

Your own package-index settings still apply, so a company mirror keeps
working:

- pip: your user and global `pip.conf` (`pip config set global.index-url …`),
  `PIP_CONFIG_FILE` and `PIP_*` variables such as `PIP_INDEX_URL`.
- uv: `UV_*` variables such as `UV_INDEX_URL` or `UV_DEFAULT_INDEX`,
  `UV_CONFIG_FILE`, and one `uv.toml`: your user file
  (`~/.config/uv/uv.toml`, or `%APPDATA%\uv\uv.toml` on Windows) if it
  exists, otherwise the system file (`/etc/uv/uv.toml`, or
  `%PROGRAMDATA%\uv\uv.toml`). uv's `cache-dir` setting is the exception:
  the launcher gives uv a cache of its own next to its environments, since uv
  installs cached files without re-checking them, unless you set
  `UV_CACHE_DIR`.

The launcher reuses that cache only when you own it, nobody else can write to
it, and it isn't a symlink. If no cache directory qualifies, each run builds a
fresh environment in a private temporary directory and deletes it afterwards,
and says why on stderr.

On Windows, step 1 is skipped: the launcher can't verify who controls a cache
directory there, so it never runs an environment left by an earlier run. Every
run builds a fresh one under `%LOCALAPPDATA%\deepgram-cli` and deletes it
afterwards. With uv that takes a few seconds; without it, about a minute each
time, so install uv. The launcher needs a real `uv.exe` or `python.exe` on
`PATH`: pyenv-win's `python.bat` shims and `uv.cmd` wrappers are skipped.

Launcher messages go to stderr, so stdout carries only the CLI's own output
(`dg ... -o json`, `dg mcp`).

## Prerequisites

One of:

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended):
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, or on Windows
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- Python 3.10 to 3.14 with `venv`. On Debian and Ubuntu that means
  `sudo apt install python3-venv`.

Node.js 20 or newer runs the launcher.

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
| `DEEPCTL_NPM_PYPI_SPEC` | Replace the requirement installed from PyPI (default: the pinned, hash-checked `requirements-<version>.txt`). Meant for testing: an override isn't hash-checked. |
| `XDG_CACHE_HOME` | Cache directory base on Linux. |

## Known limitations

### Platforms without prebuilt wheels

Setup installs prebuilt wheels only, never source builds. A few of the CLI's
dependencies don't publish wheels for every platform, so setup fails there
with "no matching distribution" or "no wheels":

- Intel Macs and Windows on ARM64: `cryptography` has no wheel for them.
- Python 3.15: not supported yet; the launcher picks Python 3.10 to 3.14.

On those machines, install the CLI another way that can build those packages
from source, such as `pipx install deepctl` or `brew install deepgram/tap/deepgram`.

### nohup and ignored signals

`nohup dg ...`, or a script that runs `dg` in the background after
`trap '' INT`, does not protect the CLI the way it protects a native program.
Node.js resets every signal its parent set to "ignore" back to the default
before the launcher's code runs, so the launcher can't tell that SIGHUP or
SIGINT was meant to be ignored, and the CLI it starts gets the defaults too.
To keep `dg` running after you close the terminal, use `setsid dg ...` (Linux),
`dg ... & disown`, or `tmux`/`screen`.

## Documentation

- CLI reference and source: <https://github.com/deepgram/cli>
- Deepgram docs: <https://developers.deepgram.com>
