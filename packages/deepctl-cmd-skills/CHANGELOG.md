# Changelog

## [0.0.9](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.8...deepctl-cmd-skills-v0.0.9) (2026-10-09)


### Features

* **skills:** install and remove only skill folders deepctl owns ([b3d629e](https://github.com/deepgram/cli/commit/b3d629e1d53a729e7ce20fbf6ecf1101648cd3a3))
* **skills:** install and remove only skill folders deepctl owns ([#124](https://github.com/deepgram/cli/issues/124)) ([5e227eb](https://github.com/deepgram/cli/commit/5e227ebf421518290626f3d3c332c860c1438532))
* **skills:** prove and remove deepctl 0.3.x skill files after the new folders land ([f65e0e2](https://github.com/deepgram/cli/commit/f65e0e22868b3d8f7ac4e81d9163045aca3383ec))
* **skills:** remove the deepctl 0.3.x skill files deepctl can prove it wrote ([#130](https://github.com/deepgram/cli/issues/130)) ([8be5172](https://github.com/deepgram/cli/commit/8be5172fc8d101dbcb60f5b1fb7b36e5f4cfbcff))
* **skills:** remove the deepctl section from GEMINI.md, instructions.md and agents.md ([b7311a3](https://github.com/deepgram/cli/commit/b7311a30e10d04d753cdb43747a5c9f0f6cde71e))


### Bug Fixes

* **skills:** hold a skills.json lock across each tool's whole install and remove ([aa07edf](https://github.com/deepgram/cli/commit/aa07edfa8ff20e19b74389441724d4fd900e1c25))
* **skills:** install skills from login and plugin through the shared installer ([1d34a22](https://github.com/deepgram/cli/commit/1d34a22c6b6c6ee2d87b3ee83657e7c28998a8fd))
* **skills:** keep a newer skills ref and a removed tool when a refresh overlaps ([#126](https://github.com/deepgram/cli/issues/126)) ([3c9d1cf](https://github.com/deepgram/cli/commit/3c9d1cf3121a649a67b74ca9992cafdb0f2159ac))
* **skills:** login and plugin install skills through the shared installer ([#125](https://github.com/deepgram/cli/issues/125)) ([3675f7a](https://github.com/deepgram/cli/commit/3675f7a21a34f59a6d1ab5c1c463149fec66d0e3))
* **skills:** re-check records in dg skills update and the plugin refresh ([b0b1595](https://github.com/deepgram/cli/commit/b0b15959214085e818a551f1c37cfb1645001c6a))
* **skills:** retain legacy copies and skip Windows cleanup ([0f103af](https://github.com/deepgram/cli/commit/0f103afc97432924e269dfcf7febc11e9abe9922))

## [0.0.8](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.7...deepctl-cmd-skills-v0.0.8) (2026-09-23)


### Bug Fixes

* **skills:** return exit code 1 when a skills command fails so automation can detect the error ([1799c21](https://github.com/deepgram/cli/commit/1799c213911f2fbc1d0c2430bb3ce7d4389c7afc))
* **skills:** return exit code 2 when a skills install is cancelled ([0497ac1](https://github.com/deepgram/cli/commit/0497ac1892333c09d2a482f0b10ccd59d1f1dba2))

## [0.0.7](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.6...deepctl-cmd-skills-v0.0.7) (2026-08-17)


### Bug Fixes

* **mcp:** swallow broken/closed-pipe on dg mcp startup notifications and error path ([#88](https://github.com/deepgram/cli/issues/88)) ([b24396e](https://github.com/deepgram/cli/commit/b24396ec3c53197b1f9e5e610c57a298926f9031))

## [0.0.6](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.5...deepctl-cmd-skills-v0.0.6) (2026-05-09)


### Bug Fixes

* uniform 'any arg = non-interactive' rule across all commands ([#78](https://github.com/deepgram/cli/issues/78)) ([6370f32](https://github.com/deepgram/cli/commit/6370f323fb3250e2d48dae9d0fe67907f1a09134))

## [0.0.5](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.4...deepctl-cmd-skills-v0.0.5) (2026-03-31)


### Features

* **skills:** fetch only repo skills, install as individual slash commands ([243ca0f](https://github.com/deepgram/cli/commit/243ca0fc427a580391153f47dabf27211342dd52))

## [0.0.4](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.3...deepctl-cmd-skills-v0.0.4) (2026-03-25)


### Features

* **skills:** fetch latest skills from deepgram/skills on every install ([b498311](https://github.com/deepgram/cli/commit/b4983116361b232ecad926ed6ced84ae84f09e37))
* **skills:** interactive tool selection for skills setup ([382b7a0](https://github.com/deepgram/cli/commit/382b7a05d47548175771b9e34f1396973d9b1e77))

## [0.0.3](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.2...deepctl-cmd-skills-v0.0.3) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))

## [0.0.2](https://github.com/deepgram/cli/compare/deepctl-cmd-skills-v0.0.1...deepctl-cmd-skills-v0.0.2) (2026-03-09)


### Features

* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* **skills:** replace CLI help dump with Deepgram Developer Guide ([4182d9b](https://github.com/deepgram/cli/commit/4182d9b20aa638ad031d9a6ed56c6f36b4aec292))


### Bug Fixes

* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))
