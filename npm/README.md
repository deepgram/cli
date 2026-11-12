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
  `%PROGRAMDATA%\uv\uv.toml`). uv's cache settings (`cache-dir` in
  `uv.toml` and `UV_CACHE_DIR`) are the exception: the launcher always
  gives uv a cache of its own, since uv installs cached files without
  re-checking them. It lives next to its environments, or, when the
  launcher builds an environment for one run only (always on Windows),
  inside that run's directory and is deleted afterwards.

The launcher reuses that cache only when you own it, nobody else can write to
it, and it isn't a symlink. The folders above it count too: if your group can
write to `~/.cache` on Linux (or `$XDG_CACHE_HOME`; umask 002 leaves it
`0775`) or to `~/Library/Caches` on macOS, the launcher keeps its cache in
`$TMPDIR/deepgram-cli-<uid>` (`/tmp` when `TMPDIR` is unset) instead, until
your temp directory is cleaned. `chmod g-w` on that folder brings it back.
If no cache directory qualifies, each run builds a fresh environment in a
private temporary directory and deletes it afterwards, and says why on stderr.

On Windows, step 1 is skipped: the launcher can't verify who controls a cache
directory there, so it never runs an environment left by an earlier run. Every
run builds a fresh one under `%LOCALAPPDATA%\deepgram-cli` and deletes it
afterwards, along with uv's cache, so each run downloads the packages again.
With uv that takes a few seconds plus the download; without it, about a
minute each time, so install uv. The launcher needs a real `uv.exe` or
`python.exe` on `PATH`: pyenv-win's `python.bat` shims and `uv.cmd` wrappers
are skipped.

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
`npx @deepgram/cli@latest`. If you added it to a project instead
(`npm install @deepgram/cli`), update it there with
`npm install @deepgram/cli@latest`: `dg update` and the update notice can't
tell the two apart, and show the global command.

## Uninstall

Your login is shared with every install method and stays after you remove
the package. To remove it too, run `dg logout --all` first. Then:

```bash
npm uninstall -g @deepgram/cli
```

(or `npm uninstall @deepgram/cli` in a project that depends on it), and
delete the launcher's cache.

macOS:

```bash
rm -rf ~/Library/Caches/deepgram-cli "${TMPDIR:-/tmp}/deepgram-cli-$(id -u)"
```

Linux:

```bash
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/deepgram-cli" "${TMPDIR:-/tmp}/deepgram-cli-$(id -u)"
```

Windows (PowerShell):

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\deepgram-cli" -ErrorAction SilentlyContinue
```

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

On those machines, install the CLI another way that builds those packages
from source. On an Intel Mac, `brew install deepgram/tap/deepgram` brings the
Rust toolchain that needs. `pipx install deepctl` and `uv tool install deepctl`
work too, once a Rust toolchain and OpenSSL are installed.

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
