# Changelog

## [0.1.0](https://github.com/deepgram/cli/compare/deepctl-cmd-models-v0.0.2...deepctl-cmd-models-v0.1.0) (2026-08-19)


### ⚠ BREAKING CHANGES

* `dg` now exits non-zero when a command fails: 1 for errors (including crashes and usage errors), 2 for user interrupt, 0 on success. Every command previously exited 0 regardless of outcome, so scripts and CI steps that ignored the exit code will surface failures they were silently swallowing. No command that succeeds changes its exit code.

### Bug Fixes

* correct web command examples, document Flux TTS/STT, and honor -o json across account commands ([#97](https://github.com/deepgram/cli/issues/97)) ([55984ec](https://github.com/deepgram/cli/commit/55984ecbc306f24c61568c3aeea59556f96b2707))
* dependency floors that let dg update skip this release, and exit-code + error-stream correctness ([#102](https://github.com/deepgram/cli/issues/102)) ([fd1e8a4](https://github.com/deepgram/cli/commit/fd1e8a4a2b34a85c37729fd80690c93b28c92281))
* **deps:** raise deepctl-core floor to 0.2.16 in the eight packages that import get_status_console ([98f9e91](https://github.com/deepgram/cli/commit/98f9e912682aff85836dbef40ee65391bb579fae))
* **keys:** honor -o json so stdout stays parseable (completes the [#97](https://github.com/deepgram/cli/issues/97) sweep) ([#101](https://github.com/deepgram/cli/issues/101)) ([e430a77](https://github.com/deepgram/cli/commit/e430a77609cbf701f52fc454907ee2ddb99dbd07))

## [0.0.2](https://github.com/deepgram/cli/compare/deepctl-cmd-models-v0.0.1...deepctl-cmd-models-v0.0.2) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))
