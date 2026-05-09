# Changelog

## [0.2.5](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.2.4...deepctl-cmd-update-v0.2.5) (2026-05-09)


### Features

* **telemetry:** full Sentry observability + per-command usage tags ([#75](https://github.com/deepgram/cli/issues/75)) ([0fe43d2](https://github.com/deepgram/cli/commit/0fe43d2e00c58d8101ef2bd4b5aaf4437db9f0cf))

## [0.2.4](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.2.3...deepctl-cmd-update-v0.2.4) (2026-03-31)


### Bug Fixes

* **update:** fix uv tool install detection when Python exe is a symlink ([6b8dd9c](https://github.com/deepgram/cli/commit/6b8dd9cdb8775ee0fa37d8f7c840d2da7d6017e2))

## [0.2.3](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.2.2...deepctl-cmd-update-v0.2.3) (2026-03-31)


### Bug Fixes

* **update:** serialize InstallMethod enum to string in all UpdateResult calls ([dddba28](https://github.com/deepgram/cli/commit/dddba289d6ae0f894af8928d3644e93492e289d8))

## [0.2.2](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.2.1...deepctl-cmd-update-v0.2.2) (2026-03-30)


### Bug Fixes

* **update:** serialize InstallMethod enum to string and fix command name ([8213065](https://github.com/deepgram/cli/commit/8213065d5602e24026a07fd3dc414cee01eb9699))

## [0.2.1](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.2.0...deepctl-cmd-update-v0.2.1) (2026-03-30)


### Bug Fixes

* **update:** correct command name to 'dg update' in update notification ([0a1b6af](https://github.com/deepgram/cli/commit/0a1b6af29dd48bcf4e00d7d8fd62d4d3e5c1571c))
* **update:** use timezone-aware datetimes to fix offset comparison error ([eb9d1d3](https://github.com/deepgram/cli/commit/eb9d1d36afb3a3a0c3390459f5a83fe7a393546d))

## [0.2.0](https://github.com/deepgram/cli/compare/deepctl-cmd-update-v0.1.10...deepctl-cmd-update-v0.2.0) (2026-03-09)


### ⚠ BREAKING CHANGES

* **core:** InstallationDetector and VersionChecker moved from deepctl_core to deepctl_cmd_update

### Features

* Add self-update functionality to deepctl ([b2152f2](https://github.com/deepgram/cli/commit/b2152f2f4f1d9cde6ac1f822dfd53ae376ae3f50))
* **core:** comprehensive architecture review and test coverage ([25a5409](https://github.com/deepgram/cli/commit/25a5409304cd3e1bbb0b8101fd717401b49ae807))
* improve update command functionality ([4a9baa0](https://github.com/deepgram/cli/commit/4a9baa04f05010a8150a4bf3bbc3b6de6eedb39a))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* **update:** add background plugin update notifications ([5dfb906](https://github.com/deepgram/cli/commit/5dfb90635fe2cd62992c46f44d50f615659e7ec8))
* **update:** overhaul update/upgrade system with expanded detection and startup notifications ([f71437a](https://github.com/deepgram/cli/commit/f71437a7316d3703786cedeb00ec28cc3a54928f))


### Bug Fixes

* resolve all ruff and mypy linting issues ([83eaa7a](https://github.com/deepgram/cli/commit/83eaa7a54093eae72e9f6f08ec021980abc2a9fd))
* **tests:** skip Unix-specific path tests on Windows ([71ee600](https://github.com/deepgram/cli/commit/71ee6004b585b072e5f4498eb7550d9243737e40))
* **tests:** use real tmp_path for pipx run detection test ([7b0a8d6](https://github.com/deepgram/cli/commit/7b0a8d64bcfba11988e6321b8372c03a16cfd5b0))
* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))
