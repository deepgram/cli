# Changelog

## [0.2.5](https://github.com/deepgram/cli/compare/deepctl-core-v0.2.4...deepctl-core-v0.2.5) (2026-03-29)


### Bug Fixes

* replace dg transcribe with dg listen across web content and skill generator ([4676d34](https://github.com/deepgram/cli/commit/4676d34dd0d5cc5603c68fd729ff7196bf8531df))

## [0.2.4](https://github.com/deepgram/cli/compare/deepctl-core-v0.2.3...deepctl-core-v0.2.4) (2026-03-29)


### Features

* **core:** add AI agent detection and --agent-friendly flag ([a5de7e2](https://github.com/deepgram/cli/commit/a5de7e24deba1521ee838aca2264f04dd2d0a933))
* **listen:** unified STT command with captions and transcribe alias ([1c6e8d8](https://github.com/deepgram/cli/commit/1c6e8d896b826d2be538c637a64517288478b748))

## [0.2.3](https://github.com/deepgram/cli/compare/deepctl-core-v0.2.2...deepctl-core-v0.2.3) (2026-03-25)


### Features

* **skills:** fetch latest skills from deepgram/skills on every install ([b498311](https://github.com/deepgram/cli/commit/b4983116361b232ecad926ed6ced84ae84f09e37))

## [0.2.2](https://github.com/deepgram/cli/compare/deepctl-core-v0.2.1...deepctl-core-v0.2.2) (2026-03-24)


### Documentation

* rewrite README with banner image, add logo to CLI help ([8c51166](https://github.com/deepgram/cli/commit/8c511663a19633b845f4284195b48fe003be0086))

## [0.2.1](https://github.com/deepgram/cli/compare/deepctl-core-v0.2.0...deepctl-core-v0.2.1) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))

## [0.2.0](https://github.com/deepgram/cli/compare/deepctl-core-v0.1.10...deepctl-core-v0.2.0) (2026-03-09)


### ⚠ BREAKING CHANGES

* **core:** InstallationDetector and VersionChecker moved from deepctl_core to deepctl_cmd_update

### Features

* add performance timing system with --timing and --timing-detailed flags ([45132ff](https://github.com/deepgram/cli/commit/45132ffa8e76ce714c8ac139dc6c40e235f39516))
* Add self-update functionality to deepctl ([b2152f2](https://github.com/deepgram/cli/commit/b2152f2f4f1d9cde6ac1f822dfd53ae376ae3f50))
* Add universal plugin support for all installation methods ([6c6d3c3](https://github.com/deepgram/cli/commit/6c6d3c3f87a3451c9474e8a1bcde6bd0148d4acc))
* **auth:** switch device flow from community server to dx-id OIDC provider ([2c04fc2](https://github.com/deepgram/cli/commit/2c04fc2001d114a85073180f7c57dddb91129596))
* complete nested command architecture implementation ([f3a9945](https://github.com/deepgram/cli/commit/f3a9945d62382696047bd45b436f67e9481dd6aa))
* **core:** comprehensive architecture review and test coverage ([25a5409](https://github.com/deepgram/cli/commit/25a5409304cd3e1bbb0b8101fd717401b49ae807))
* enhance performance timing and core architecture ([830aa47](https://github.com/deepgram/cli/commit/830aa47b67398e7758f31b3957c62a400ae822e9))
* enhance test infrastructure with flexible running options ([d42a5e9](https://github.com/deepgram/cli/commit/d42a5e95a1d273378c82a2648f914c3b034d8cbe))
* **ffprobe:** add audio analysis via ffprobe across CLI ([8e2c923](https://github.com/deepgram/cli/commit/8e2c92387815b08f86373c49ab6c435f9d18d846))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **plugin:** add install-aware plugin system with venv bridge and strategy pattern ([7dd849b](https://github.com/deepgram/cli/commit/7dd849b6d5cc66fc6a9d54d665aad8909830b5ef))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* **skills:** replace CLI help dump with Deepgram Developer Guide ([4182d9b](https://github.com/deepgram/cli/commit/4182d9b20aa638ad031d9a6ed56c6f36b4aec292))
* update authentication and core modules ([1a1df1c](https://github.com/deepgram/cli/commit/1a1df1cf0db70c902eb8c1ef3c54cc17d980605b))
* update authentication and security implementation ([44d5568](https://github.com/deepgram/cli/commit/44d5568ebc8d50bf2b6d3ed31e2248da38d2565c))
* **update:** overhaul update/upgrade system with expanded detection and startup notifications ([f71437a](https://github.com/deepgram/cli/commit/f71437a7316d3703786cedeb00ec28cc3a54928f))


### Bug Fixes

* add explicit exports to core models ([2827f9a](https://github.com/deepgram/cli/commit/2827f9a35b5cb93086ced446cdc76be7f61f300b))
* **auth:** decouple API key validation from project ID requirement ([0f67b22](https://github.com/deepgram/cli/commit/0f67b225217f2ddeb5ef29109f878fdb4d3b589d))
* **auth:** open browser and start polling immediately on login ([ecb8271](https://github.com/deepgram/cli/commit/ecb82713049b3aa348ebae8507d463ae9a063e70))
* configure pytest to run tests across all workspace packages ([17e1ba6](https://github.com/deepgram/cli/commit/17e1ba6230894dc4122dec55f9c23f73df886a66))
* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))
* **skills:** fix entry point iteration in collect_command_metadata ([7b8424f](https://github.com/deepgram/cli/commit/7b8424f91e86ff10ba591d5a58333adf7a2b795f))
* **tests:** make config tests cross-platform compatible for Windows ([701263d](https://github.com/deepgram/cli/commit/701263dacb78aa2f17e9713b56e131b776cf7821))
* **tests:** skip Unix-specific path tests on Windows ([71ee600](https://github.com/deepgram/cli/commit/71ee6004b585b072e5f4498eb7550d9243737e40))
* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))
* **transcribe:** fix SDK response serialization and redesign output ([908097c](https://github.com/deepgram/cli/commit/908097cfd3f1323d67eaa797bd00c037accfb629))
