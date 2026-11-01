# Telemetry

dg sends one error report when a command fails with an unexpected error. Nothing is sent when a command succeeds, for a usage error or another error dg reports to you, or for `--help` and `--version`. The README's Telemetry section says where reports go.

## When it runs

- **At most one report per run**, and only for an unexpected error.
- **No network or reporting code on the success path.** The reporting library is loaded only after an unexpected error, so a command that succeeds opens no connection for telemetry.
- **A short time budget.** dg waits about 0.6 s at most for a report, loading included, and doesn't wait at all when no report was queued. With blocked or slow egress, a failing command takes at most that much longer.

## Turn it off

Any one of these turns telemetry off. They are checked in this order:

| Switch | Scope |
|---|---|
| `--no-telemetry` | One command |
| `DEEPCTL_TELEMETRY_DISABLED=1` | A shell session, a CI job |
| `DEEPCTL_TELEMETRY=off` | A shell session, a CI job |
| `DO_NOT_TRACK=1` | Every tool that follows the [Console Do Not Track](https://consoledonottrack.com/) convention |
| `CI` set (as CI services do) | Any CI job |
| `telemetry.enabled: false` in your `config.yaml` | Always |

For `DEEPCTL_TELEMETRY_DISABLED`, `DO_NOT_TRACK` and `CI`, any value except an empty one, `0`, `false`, `no` or `off` turns telemetry off. `DEEPCTL_TELEMETRY` turns it off when set to `off`, `0`, `false`, `no` or `disabled`; `log` is described below, and any other value changes nothing. A config file dg won't load (unreadable, too large, not valid YAML, a repeated `telemetry` key, or one dg doesn't trust in an AI agent session) counts as turned off too. A project's `deepgram.yaml` can't change these settings. The last line of `dg --help` shows whether telemetry is on.

## See what would be sent

Run a command with `DEEPCTL_TELEMETRY=log`. If it fails with an unexpected error and telemetry is on, dg prints the report it would have sent to stderr, as one JSON line, and sends nothing.

```sh
DEEPCTL_TELEMETRY=log dg listen recording.wav
```

## Field schema

A report holds these fields and nothing else. A value that doesn't match its expected shape (an identifier, a version string, a command path) is dropped, never shortened or escaped. Names from code outside dg and the libraries it calls, such as a plugin's, are sent as `<other>`.

<!-- fields:start -->
| Field | Contents |
|---|---|
| `event_id` | Random report ID (32 hex characters) |
| `timestamp` | When the error happened |
| `platform` | Always `python` |
| `level` | Always `error` |
| `release` | `deepctl@` and the dg version |
| `environment` | Always `production` |
| `sdk.name` | Name of the reporting library |
| `sdk.version` | Version of the reporting library |
| `tags.cmd.path` | Command path, such as `dg listen` |
| `tags.cmd.flags` | Names of the flags used, never their values, such as `--model,--output` |
| `tags.cli.os` | Operating system, such as `Darwin` |
| `tags.cli.arch` | CPU architecture, such as `arm64` |
| `tags.cli.python` | Python version, such as `3.12.4` |
| `exception.values[].type` | Exception type, and the type of each exception that led to it |
| `exception.values[].mechanism.type` | How the error was caught, such as `generic` |
| `exception.values[].mechanism.handled` | Whether dg caught it |
| `exception.values[].stacktrace.frames[].module` | Module of each stack frame |
| `exception.values[].stacktrace.frames[].function` | Function of each stack frame |
| `exception.values[].stacktrace.frames[].lineno` | Line number of each stack frame |
<!-- fields:end -->

A report never holds flag values or arguments, file names or paths, environment variables, API keys, request or response data, error messages, log output, variable values, source code, your hostname or your username.

A test (`packages/deepctl-telemetry/tests/unit/test_contract.py`) sends a report through the real error path and checks that its fields are exactly the ones in this table.
