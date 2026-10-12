# Changelog

## [0.1.14](https://github.com/deepgram/cli/compare/deepctl-cmd-plugin-v0.1.13...deepctl-cmd-plugin-v0.1.14) (2026-10-09)


### Features

* **skills:** prove and remove deepctl 0.3.x skill files after the new folders land ([f65e0e2](https://github.com/deepgram/cli/commit/f65e0e22868b3d8f7ac4e81d9163045aca3383ec))
* **skills:** remove the deepctl 0.3.x skill files deepctl can prove it wrote ([#130](https://github.com/deepgram/cli/issues/130)) ([8be5172](https://github.com/deepgram/cli/commit/8be5172fc8d101dbcb60f5b1fb7b36e5f4cfbcff))


### Bug Fixes

* **skills:** install skills from login and plugin through the shared installer ([1d34a22](https://github.com/deepgram/cli/commit/1d34a22c6b6c6ee2d87b3ee83657e7c28998a8fd))
* **skills:** keep a newer skills ref and a removed tool when a refresh overlaps ([#126](https://github.com/deepgram/cli/issues/126)) ([3c9d1cf](https://github.com/deepgram/cli/commit/3c9d1cf3121a649a67b74ca9992cafdb0f2159ac))
* **skills:** login and plugin install skills through the shared installer ([#125](https://github.com/deepgram/cli/issues/125)) ([3675f7a](https://github.com/deepgram/cli/commit/3675f7a21a34f59a6d1ab5c1c463149fec66d0e3))
* **skills:** re-check records in dg skills update and the plugin refresh ([b0b1595](https://github.com/deepgram/cli/commit/b0b15959214085e818a551f1c37cfb1645001c6a))

## [0.1.13](https://github.com/deepgram/cli/compare/deepctl-cmd-plugin-v0.1.12...deepctl-cmd-plugin-v0.1.13) (2026-09-23)


### Bug Fixes

* **plugin:** return exit code 2, rather than success, when plugin removal is cancelled ([0dcfd2c](https://github.com/deepgram/cli/commit/0dcfd2c0d68a6095f4f6651e51ba6995f2b430d3))
* **plugin:** write the plugin-removal confirmation prompt to stderr so JSON output stays parseable ([0497ac1](https://github.com/deepgram/cli/commit/0497ac1892333c09d2a482f0b10ccd59d1f1dba2))

## [0.1.12](https://github.com/deepgram/cli/compare/deepctl-cmd-plugin-v0.1.11...deepctl-cmd-plugin-v0.1.12) (2026-05-09)


### Features

* **telemetry:** full Sentry observability + per-command usage tags ([#75](https://github.com/deepgram/cli/issues/75)) ([0fe43d2](https://github.com/deepgram/cli/commit/0fe43d2e00c58d8101ef2bd4b5aaf4437db9f0cf))

## [0.1.11](https://github.com/deepgram/cli/compare/deepctl-cmd-plugin-v0.1.10...deepctl-cmd-plugin-v0.1.11) (2026-03-09)


### Features

* Add plugin search command with hardcoded registry ([bd533cb](https://github.com/deepgram/cli/commit/bd533cb4b90b531fc522c4b2fd9a582d05d7861a))
* Add universal plugin support for all installation methods ([6c6d3c3](https://github.com/deepgram/cli/commit/6c6d3c3f87a3451c9474e8a1bcde6bd0148d4acc))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **plugin:** add install-aware plugin system with venv bridge and strategy pattern ([7dd849b](https://github.com/deepgram/cli/commit/7dd849b6d5cc66fc6a9d54d665aad8909830b5ef))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))


### Bug Fixes

* Remove duplicate entries in plugin list ([11a3fe3](https://github.com/deepgram/cli/commit/11a3fe340e320df46c3d6ed872f469205b32163f))
* resolve all ruff and mypy linting issues ([83eaa7a](https://github.com/deepgram/cli/commit/83eaa7a54093eae72e9f6f08ec021980abc2a9fd))
* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))
