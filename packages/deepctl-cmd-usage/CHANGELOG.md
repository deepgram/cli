# Changelog

## [0.2.0](https://github.com/deepgram/cli/compare/deepctl-cmd-usage-v0.1.13...deepctl-cmd-usage-v0.2.0) (2026-08-19)


### ⚠ BREAKING CHANGES

* `dg` now exits non-zero when a command fails: 1 for errors (including crashes and usage errors), 2 for user interrupt, 0 on success. Every command previously exited 0 regardless of outcome, so scripts and CI steps that ignored the exit code will surface failures they were silently swallowing. No command that succeeds changes its exit code.

### Bug Fixes

* correct web command examples, document Flux TTS/STT, and honor -o json across account commands ([#97](https://github.com/deepgram/cli/issues/97)) ([55984ec](https://github.com/deepgram/cli/commit/55984ecbc306f24c61568c3aeea59556f96b2707))
* dependency floors that let dg update skip this release, and exit-code + error-stream correctness ([#102](https://github.com/deepgram/cli/issues/102)) ([fd1e8a4](https://github.com/deepgram/cli/commit/fd1e8a4a2b34a85c37729fd80690c93b28c92281))
* **deps:** raise deepctl-core floor to 0.2.16 in the eight packages that import get_status_console ([98f9e91](https://github.com/deepgram/cli/commit/98f9e912682aff85836dbef40ee65391bb579fae))
* **keys:** honor -o json so stdout stays parseable (completes the [#97](https://github.com/deepgram/cli/issues/97) sweep) ([#101](https://github.com/deepgram/cli/issues/101)) ([e430a77](https://github.com/deepgram/cli/commit/e430a77609cbf701f52fc454907ee2ddb99dbd07))

## [0.1.13](https://github.com/deepgram/cli/compare/deepctl-cmd-usage-v0.1.12...deepctl-cmd-usage-v0.1.13) (2026-08-17)


### Features

* SDK 7.7.0 — Flux TTS controls, Flux STT fix, listen redact/numerals ([#92](https://github.com/deepgram/cli/issues/92)) ([50d96cf](https://github.com/deepgram/cli/commit/50d96cf8950c9f180619e0e2dbd41931d1a63ef6))

## [0.1.12](https://github.com/deepgram/cli/compare/deepctl-cmd-usage-v0.1.11...deepctl-cmd-usage-v0.1.12) (2026-07-15)


### Features

* **speak:** Flux TTS (Speak v2 WebSocket streaming) ([#86](https://github.com/deepgram/cli/issues/86)) ([15526ac](https://github.com/deepgram/cli/commit/15526ac08a6b931f260223123c2cfe6b5cc08ec0))

## [0.1.11](https://github.com/deepgram/cli/compare/deepctl-cmd-usage-v0.1.10...deepctl-cmd-usage-v0.1.11) (2026-03-09)


### Features

* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* update authentication and core modules ([1a1df1c](https://github.com/deepgram/cli/commit/1a1df1cf0db70c902eb8c1ef3c54cc17d980605b))
* **update:** overhaul update/upgrade system with expanded detection and startup notifications ([f71437a](https://github.com/deepgram/cli/commit/f71437a7316d3703786cedeb00ec28cc3a54928f))


### Bug Fixes

* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))
