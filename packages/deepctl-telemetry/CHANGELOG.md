# Changelog

## [0.0.8](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.7...deepctl-telemetry-v0.0.8) (2026-11-12)


### Bug Fixes

* **config:** limit what a project deepgram.yaml or a non-default --config file can set ([a71a918](https://github.com/deepgram/cli/commit/a71a9189c7f1ff92b22a18e36edf3a405af98eaf))
* **config:** read and write the config file safely ([36805f2](https://github.com/deepgram/cli/commit/36805f21c99b1554180fe90a4bcd8a5671e2bd38))
* **telemetry:** stop sending error reports ([70033dd](https://github.com/deepgram/cli/commit/70033ddbdb59235a5418ef103de2185bab13f6fe))

## [0.0.7](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.6...deepctl-telemetry-v0.0.7) (2026-09-29)


### Bug Fixes

* **docs:** correct 11 advertised commands the CLI rejects, and guard them ([9ef3b58](https://github.com/deepgram/cli/commit/9ef3b5850b4e124a9c29bc744c0e575e2cec28e7))
* keep stdout parseable, correct advertised examples and the auto-JSON claim ([#107](https://github.com/deepgram/cli/issues/107)) ([9d434a3](https://github.com/deepgram/cli/commit/9d434a3b8cf4fb2d5aa2777eace35b969df9834e))

## [0.0.6](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.5...deepctl-telemetry-v0.0.6) (2026-08-17)


### Bug Fixes

* **mcp:** swallow broken/closed-pipe on dg mcp startup notifications and error path ([#88](https://github.com/deepgram/cli/issues/88)) ([b24396e](https://github.com/deepgram/cli/commit/b24396ec3c53197b1f9e5e610c57a298926f9031))

## [0.0.5](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.4...deepctl-telemetry-v0.0.5) (2026-05-12)


### Bug Fixes

* **mcp:** survive stdio host disconnect and silence handled MCP noise ([#79](https://github.com/deepgram/cli/issues/79)) ([e95d511](https://github.com/deepgram/cli/commit/e95d5110c055eceb4717200eb12b91aa933fa38f))

## [0.0.4](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.3...deepctl-telemetry-v0.0.4) (2026-05-09)


### Features

* **telemetry:** full Sentry observability + per-command usage tags ([#75](https://github.com/deepgram/cli/issues/75)) ([0fe43d2](https://github.com/deepgram/cli/commit/0fe43d2e00c58d8101ef2bd4b5aaf4437db9f0cf))
* **telemetry:** turn on full Sentry observability for the CLI ([cc2c208](https://github.com/deepgram/cli/commit/cc2c2080d8b7ea22b5cb25d954d31b6cc0fe489d))


### Bug Fixes

* **telemetry:** explicitly start session after init ([8dd2843](https://github.com/deepgram/cli/commit/8dd2843c18014c8e9712768ee04e51318440da98))
* **telemetry:** explicitly start session after init ([#74](https://github.com/deepgram/cli/issues/74)) ([ad75efa](https://github.com/deepgram/cli/commit/ad75efa541d79145e6fbd95e7eabcbc666b854d8))

## [0.0.3](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.2...deepctl-telemetry-v0.0.3) (2026-05-08)


### Features

* **telemetry:** enable session tracking and flush envelopes on exit ([8938761](https://github.com/deepgram/cli/commit/8938761f14c47ffc985a47f90f10ce5d03895546))

## [0.0.2](https://github.com/deepgram/cli/compare/deepctl-telemetry-v0.0.1...deepctl-telemetry-v0.0.2) (2026-05-07)


### Features

* **telemetry:** opt-out phone-home telemetry for the CLI and cli.deepgram.com ([#59](https://github.com/deepgram/cli/issues/59)) ([8e20b16](https://github.com/deepgram/cli/commit/8e20b16316c7638af42db086cde0bf27dae276ea))

## Changelog
