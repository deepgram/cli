# Changelog

## [0.1.13](https://github.com/deepgram/cli/compare/deepctl-cmd-debug-audio-v0.1.12...deepctl-cmd-debug-audio-v0.1.13) (2026-05-09)


### Features

* **telemetry:** full Sentry observability + per-command usage tags ([#75](https://github.com/deepgram/cli/issues/75)) ([0fe43d2](https://github.com/deepgram/cli/commit/0fe43d2e00c58d8101ef2bd4b5aaf4437db9f0cf))

## [0.1.12](https://github.com/deepgram/cli/compare/deepctl-cmd-debug-audio-v0.1.11...deepctl-cmd-debug-audio-v0.1.12) (2026-03-31)


### Bug Fixes

* **debug:** correct debug audio example to use --file/-f flag ([42dcdad](https://github.com/deepgram/cli/commit/42dcdad195ecf000fb1304fd79885109269739ce))

## [0.1.11](https://github.com/deepgram/cli/compare/deepctl-cmd-debug-audio-v0.1.10...deepctl-cmd-debug-audio-v0.1.11) (2026-03-09)


### Features

* **api,debug:** add `deepctl api` command, `debug stream` proxy, and debug improvements ([53accd5](https://github.com/deepgram/cli/commit/53accd5a30754711fbe615e9294d52c08546bbcc))
* complete nested command architecture implementation ([f3a9945](https://github.com/deepgram/cli/commit/f3a9945d62382696047bd45b436f67e9481dd6aa))
* **debug-audio:** add ffmpeg-python dependency for audio file analysis ([398b2eb](https://github.com/deepgram/cli/commit/398b2eb1791c0fcbc662842d7760228d69f6bb90))
* **debug-audio:** implement comprehensive audio file analysis ([fb773b6](https://github.com/deepgram/cli/commit/fb773b6ab27f738741ade32851df959c8a9fe29d))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))


### Bug Fixes

* **debug-audio:** correct indentation in error handling blocks ([f8931a2](https://github.com/deepgram/cli/commit/f8931a22515fc1bd4018443ab78885e5973d21cd))
* **debug-audio:** restore correct indentation in handle method ([e40cf29](https://github.com/deepgram/cli/commit/e40cf298ece2df72b1ee2b91f723349b3be3f1fe))
* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))
* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))


### Documentation

* **debug-audio:** add comprehensive documentation ([6a32c2b](https://github.com/deepgram/cli/commit/6a32c2b379a5bdff4342a649d5d95aa408204aa9))
* **readme:** regenerate READMEs for new api and debug-stream packages ([bd7170a](https://github.com/deepgram/cli/commit/bd7170af5548b36a7b0ea46fc7031503d9251837))
