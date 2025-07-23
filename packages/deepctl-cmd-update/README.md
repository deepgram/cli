# deepctl-cmd-update

Update command for deepctl CLI - provides self-update functionality.

## Features

- Check for newer versions on PyPI
- Detect installation method (pip, pipx, uv, system, development)
- Remember installation method for future updates
- Provide appropriate update commands
- Support for various installation scenarios

## Installation

This package is included with deepctl and doesn't need separate installation.

## Usage

Check for updates:

```bash
deepctl update --check-only
```

Update deepctl:

```bash
deepctl update
```

Force update (even if already up to date):

```bash
deepctl update --force
```

Skip confirmation:

```bash
deepctl update --yes
```

## Development

This package is part of the deepctl monorepo. See the main repository for development instructions.
