# Changelog

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
