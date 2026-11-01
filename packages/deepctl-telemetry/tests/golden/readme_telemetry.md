## Telemetry

This version of dg sends no telemetry: no error reports, usage data or other telemetry, whether a command succeeds or fails. dg still contacts the Deepgram API for the commands you run, and pypi.org to check for updates.

The `DEEPCTL_TELEMETRY_DISABLED` environment variable and the `telemetry.enabled` setting in `config.yaml` are still accepted, and the new `--no-telemetry` flag is accepted too, but none of them has an effect in this version. `DEEPCTL_TELEMETRY_DSN` is now ignored unless it points at the local machine, which is for tests. The last line of `dg --help` says that dg sends no telemetry.
