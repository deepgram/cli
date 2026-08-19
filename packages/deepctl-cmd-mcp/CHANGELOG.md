# Changelog

## [0.1.15](https://github.com/deepgram/cli/compare/deepctl-cmd-mcp-v0.1.14...deepctl-cmd-mcp-v0.1.15) (2026-08-19)


### Bug Fixes

* **deps:** cap mcp &lt;2 (fixes broken dg mcp), commit uv.lock, require twine &gt;=7 ([#95](https://github.com/deepgram/cli/issues/95)) ([997cd36](https://github.com/deepgram/cli/commit/997cd36f64c95d8afadb4b9fb86673153169ce1f))

## [0.1.14](https://github.com/deepgram/cli/compare/deepctl-cmd-mcp-v0.1.13...deepctl-cmd-mcp-v0.1.14) (2026-05-12)


### Bug Fixes

* **mcp:** survive stdio host disconnect and silence handled MCP noise ([#79](https://github.com/deepgram/cli/issues/79)) ([e95d511](https://github.com/deepgram/cli/commit/e95d5110c055eceb4717200eb12b91aa933fa38f))

## [0.1.13](https://github.com/deepgram/cli/compare/deepctl-cmd-mcp-v0.1.12...deepctl-cmd-mcp-v0.1.13) (2026-05-01)


### Features

* **deepctl-core:** use dg alias in plugin venv ABI mismatch warning ([#47](https://github.com/deepgram/cli/issues/47)) ([c7c61c0](https://github.com/deepgram/cli/commit/c7c61c04cb17889b9fbbb2d65305e685d9cb380e))

## [0.1.12](https://github.com/deepgram/cli/compare/deepctl-cmd-mcp-v0.1.11...deepctl-cmd-mcp-v0.1.12) (2026-03-25)


### Bug Fixes

* **mcp:** update default base URL to api.dx.deepgram.com ([c514545](https://github.com/deepgram/cli/commit/c514545c52622f516eb51703fa787fa0d68751ff))

## [0.1.11](https://github.com/deepgram/cli/compare/deepctl-cmd-mcp-v0.1.10...deepctl-cmd-mcp-v0.1.11) (2026-03-09)


### Features

* add MCP server command for Gnosis AI integration ([4babddf](https://github.com/deepgram/cli/commit/4babddf09c32c2cb954c9224f500ff6bba0ac0ae))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **mcp:** rearchitect as proxy to dx-api MCP endpoint ([65a62f4](https://github.com/deepgram/cli/commit/65a62f4716a1b92d27c82a0e93d922cca522c24e))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))


### Bug Fixes

* handle graceful shutdown of MCP server to prevent threading errors ([0fbb772](https://github.com/deepgram/cli/commit/0fbb772b36c08254fe948eabc92f2a75362c3929))
* improve MCP server shutdown handling to work with single Ctrl+C ([7bc1d94](https://github.com/deepgram/cli/commit/7bc1d94eee4ca83ce6d27c23a48cc1b7c5f2213a))
* make MCP server actually exit on first Ctrl+C ([4b4a245](https://github.com/deepgram/cli/commit/4b4a245e96543bf77100f93d4779681a313deed9))
* MCP server now exits cleanly on first Ctrl+C ([49c4aca](https://github.com/deepgram/cli/commit/49c4aca98812d2030c08091f804f58f0ad1d13e0))
* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))


### Documentation

* fix critical inconsistencies between documentation and code ([6455932](https://github.com/deepgram/cli/commit/6455932c4d1e950107279a44f6070330a8b7cde6))
