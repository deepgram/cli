# Changelog

## [0.1.17](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.16...deepctl-cmd-login-v0.1.17) (2026-08-17)


### Features

* SDK 7.7.0 — Flux TTS controls, Flux STT fix, listen redact/numerals ([#92](https://github.com/deepgram/cli/issues/92)) ([50d96cf](https://github.com/deepgram/cli/commit/50d96cf8950c9f180619e0e2dbd41931d1a63ef6))

## [0.1.16](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.15...deepctl-cmd-login-v0.1.16) (2026-07-15)


### Features

* **speak:** Flux TTS (Speak v2 WebSocket streaming) ([#86](https://github.com/deepgram/cli/issues/86)) ([15526ac](https://github.com/deepgram/cli/commit/15526ac08a6b931f260223123c2cfe6b5cc08ec0))

## [0.1.15](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.14...deepctl-cmd-login-v0.1.15) (2026-05-09)


### Features

* **telemetry:** full Sentry observability + per-command usage tags ([#75](https://github.com/deepgram/cli/issues/75)) ([0fe43d2](https://github.com/deepgram/cli/commit/0fe43d2e00c58d8101ef2bd4b5aaf4437db9f0cf))


### Bug Fixes

* uniform 'any arg = non-interactive' rule across all commands ([#78](https://github.com/deepgram/cli/issues/78)) ([6370f32](https://github.com/deepgram/cli/commit/6370f323fb3250e2d48dae9d0fe67907f1a09134))

## [0.1.14](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.13...deepctl-cmd-login-v0.1.14) (2026-03-29)


### Features

* **cli:** add whoami, --dry-run, and shell completion ([5577ef2](https://github.com/deepgram/cli/commit/5577ef2a37049cfddf227fd14d5705607ad056d0))

## [0.1.13](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.12...deepctl-cmd-login-v0.1.13) (2026-03-25)


### Features

* **skills:** fetch latest skills from deepgram/skills on every install ([b498311](https://github.com/deepgram/cli/commit/b4983116361b232ecad926ed6ced84ae84f09e37))
* **skills:** interactive tool selection for skills setup ([382b7a0](https://github.com/deepgram/cli/commit/382b7a05d47548175771b9e34f1396973d9b1e77))

## [0.1.12](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.11...deepctl-cmd-login-v0.1.12) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))

## [0.1.11](https://github.com/deepgram/cli/compare/deepctl-cmd-login-v0.1.10...deepctl-cmd-login-v0.1.11) (2026-03-09)


### Features

* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* update authentication and core modules ([1a1df1c](https://github.com/deepgram/cli/commit/1a1df1cf0db70c902eb8c1ef3c54cc17d980605b))
* update authentication and security implementation ([44d5568](https://github.com/deepgram/cli/commit/44d5568ebc8d50bf2b6d3ed31e2248da38d2565c))
* **update:** overhaul update/upgrade system with expanded detection and startup notifications ([f71437a](https://github.com/deepgram/cli/commit/f71437a7316d3703786cedeb00ec28cc3a54928f))


### Bug Fixes

* **auth:** decouple API key validation from project ID requirement ([0f67b22](https://github.com/deepgram/cli/commit/0f67b225217f2ddeb5ef29109f878fdb4d3b589d))
* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))
