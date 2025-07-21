# deepctl-cmd-debug-audio

Audio debug subcommand for the Deepgram CLI (deepctl).

## Overview

This package provides the `audio` subcommand under the `debug` command group, allowing users to debug audio file issues when using Deepgram's transcription services.

## Installation

This package is automatically installed as part of deepctl and should not be installed separately.

## Usage

```bash
# Debug audio file
deepctl debug audio

# Alternative hyphenated syntax
deepctl debug-audio
```

## Features

- Audio file debugging utilities (stub implementation)

## Development

This command extends `BaseCommand` and is registered as a subcommand of the `debug` group via the entry point `deepctl.subcommands.debug`.
