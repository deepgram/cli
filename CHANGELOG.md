# Changelog

## [0.2.22](https://github.com/deepgram/cli/compare/v0.2.21...v0.2.22) (2026-05-08)


### Bug Fixes

* **listen:** restrict feature prompt to bare-invocation guided flow ([6ea275b](https://github.com/deepgram/cli/commit/6ea275b82c639b705ce13baa2f851c16b02c8082))
* **listen:** restrict feature prompt to bare-invocation guided flow ([#68](https://github.com/deepgram/cli/issues/68)) ([ee92d1f](https://github.com/deepgram/cli/commit/ee92d1f802277980c32ad95385c9454a679b78c2))
* **non-interactive:** promote CI to hard signal, add --non-interactive flag ([8da4e21](https://github.com/deepgram/cli/commit/8da4e2155248e17d9031596be112bed7b804cbda))
* **non-interactive:** promote CI=1 to hard signal, add --non-interactive flag ([#67](https://github.com/deepgram/cli/issues/67)) ([a25099d](https://github.com/deepgram/cli/commit/a25099d388edce7577a1ac81c7889b1eeffdd667))

## [0.2.21](https://github.com/deepgram/cli/compare/v0.2.20...v0.2.21) (2026-05-07)


### Features

* delete .cursor directory ([2029795](https://github.com/deepgram/cli/commit/202979589dd86a77b73991a0a28fdce9312b4963))
* **telemetry:** opt-out phone-home telemetry for the CLI and cli.deepgram.com ([#59](https://github.com/deepgram/cli/issues/59)) ([8e20b16](https://github.com/deepgram/cli/commit/8e20b16316c7638af42db086cde0bf27dae276ea))

## [0.2.20](https://github.com/deepgram/cli/compare/v0.2.19...v0.2.20) (2026-05-02)


### Bug Fixes

* **brew:** add rust + pkgconf as build-time deps in formula template ([#49](https://github.com/deepgram/cli/issues/49)) ([82dd473](https://github.com/deepgram/cli/commit/82dd4738d959c0f8cb6cbbb70e4933975235600d))

## [0.2.19](https://github.com/deepgram/cli/compare/v0.2.18...v0.2.19) (2026-05-01)


### Features

* **debug:** add toolkit subcommand for deepgram/support-toolkit scripts ([768f34f](https://github.com/deepgram/cli/commit/768f34f485a499a2433596094bc5c7bada416361))
* **deepctl-core:** use dg alias in plugin venv ABI mismatch warning ([#47](https://github.com/deepgram/cli/issues/47)) ([c7c61c0](https://github.com/deepgram/cli/commit/c7c61c04cb17889b9fbbb2d65305e685d9cb380e))


### Documentation

* add dx-stack rules to CLAUDE.md, add cross-stack hook ([f4fbf3e](https://github.com/deepgram/cli/commit/f4fbf3e69382d47f1ac195e7a1068c658892eb93))

## [0.2.18](https://github.com/deepgram/cli/compare/v0.2.17...v0.2.18) (2026-04-04)


### Features

* **auth:** implement JWT-based device flow with silent token refresh ([0817978](https://github.com/deepgram/cli/commit/08179787f87ba10ca881541495519ff6fdd8ec99))


### Bug Fixes

* **deps:** resolve deepgram-mcp from PyPI instead of local path ([dfe0030](https://github.com/deepgram/cli/commit/dfe003017f1ad436e7233277f7911502015c4d51))

## [0.2.17](https://github.com/deepgram/cli/compare/v0.2.16...v0.2.17) (2026-03-31)


### Bug Fixes

* **debug:** correct debug audio example to use --file/-f flag ([42dcdad](https://github.com/deepgram/cli/commit/42dcdad195ecf000fb1304fd79885109269739ce))
* **listen:** emit metadata as WebVTT NOTE block in header, suppress status lines during caption streams ([58808e2](https://github.com/deepgram/cli/commit/58808e282090716aa0f808fa63bce82aa872813b))
* **listen:** show captions before JSON output path so --webvtt and --srt work on pre-recorded files ([16b4445](https://github.com/deepgram/cli/commit/16b44457fb7e1a4bbc5e8452358ab1029712ee57))
* **listen:** suppress JSON summary after live caption streams (--mic/stdin --webvtt/--srt) ([27c29e7](https://github.com/deepgram/cli/commit/27c29e759f44fb6055045a37800c099ea4f5cd8c))

## [0.2.16](https://github.com/deepgram/cli/compare/v0.2.15...v0.2.16) (2026-03-31)


### Features

* **skills:** fetch only repo skills, install as individual slash commands ([243ca0f](https://github.com/deepgram/cli/commit/243ca0fc427a580391153f47dabf27211342dd52))
* **web:** add all-commands showcase section to landing page ([2e8c843](https://github.com/deepgram/cli/commit/2e8c84327b7f95d069bdc4ed48d906cd3591e75a))


### Bug Fixes

* **ci:** deploy staging on every push to main, not just web changes ([399ceba](https://github.com/deepgram/cli/commit/399cebaf89df3e42044e7179e74a0e97d75bfe27))

## [0.2.15](https://github.com/deepgram/cli/compare/v0.2.14...v0.2.15) (2026-03-31)


### Features

* **update:** add hidden --record-install-method flag for install scripts ([07f5035](https://github.com/deepgram/cli/commit/07f50358d2f025b3a689c64fab37c1dd1afb48bb))


### Bug Fixes

* **release:** ensure root package is always marked as latest ([869f413](https://github.com/deepgram/cli/commit/869f413bcfbfe20898b3747dfb9b9ceb3d162d10))
* **update:** fix uv tool install detection when Python exe is a symlink ([6b8dd9c](https://github.com/deepgram/cli/commit/6b8dd9cdb8775ee0fa37d8f7c840d2da7d6017e2))

## [0.2.14](https://github.com/deepgram/cli/compare/v0.2.13...v0.2.14) (2026-03-31)


### Bug Fixes

* **listen:** make sounddevice a hard dep and surface mic errors ([6b3b088](https://github.com/deepgram/cli/commit/6b3b0882ff28bf9959ee7c3e3dcd1f9b8ad41dcc))
* **update:** serialize InstallMethod enum to string in all UpdateResult calls ([dddba28](https://github.com/deepgram/cli/commit/dddba289d6ae0f894af8928d3644e93492e289d8))

## [0.2.13](https://github.com/deepgram/cli/compare/v0.2.12...v0.2.13) (2026-03-30)


### Bug Fixes

* **listen:** fix topics parameter name and add sentiment display ([8974b6c](https://github.com/deepgram/cli/commit/8974b6c9e6029b6da4bc0f109afaa4d5f7272c8f))

## [0.2.12](https://github.com/deepgram/cli/compare/v0.2.11...v0.2.12) (2026-03-30)


### Bug Fixes

* **update:** serialize InstallMethod enum to string and fix command name ([8213065](https://github.com/deepgram/cli/commit/8213065d5602e24026a07fd3dc414cee01eb9699))

## [0.2.11](https://github.com/deepgram/cli/compare/v0.2.10...v0.2.11) (2026-03-30)


### Bug Fixes

* **listen:** fix three root causes of missing transcript output ([274aa41](https://github.com/deepgram/cli/commit/274aa41476dfce90c05f08550e3db71800ae19e3))

## [0.2.10](https://github.com/deepgram/cli/compare/v0.2.9...v0.2.10) (2026-03-30)


### Features

* **test:** add tests for check_prereqs tool detection in init lifecycle ([73eac9b](https://github.com/deepgram/cli/commit/73eac9b14f1f9b292f191a2432d4327d453d5234))

## [0.2.9](https://github.com/deepgram/cli/compare/v0.2.8...v0.2.9) (2026-03-30)


### Bug Fixes

* **update:** correct command name to 'dg update' in update notification ([0a1b6af](https://github.com/deepgram/cli/commit/0a1b6af29dd48bcf4e00d7d8fd62d4d3e5c1571c))
* **update:** use timezone-aware datetimes to fix offset comparison error ([eb9d1d3](https://github.com/deepgram/cli/commit/eb9d1d36afb3a3a0c3390459f5a83fe7a393546d))

## [0.2.8](https://github.com/deepgram/cli/compare/v0.2.7...v0.2.8) (2026-03-30)


### Features

* **init:** prereq checking, search-and-select picker, make init integration ([7de0189](https://github.com/deepgram/cli/commit/7de018916001a28962bee5040c5175a216458264))


### Bug Fixes

* **listen:** handle Pydantic model_dump() None values in transcript extraction ([39229b0](https://github.com/deepgram/cli/commit/39229b03b1fc2bedf38701bd3c467a0d5ef42b25))

## [0.2.7](https://github.com/deepgram/cli/compare/v0.2.6...v0.2.7) (2026-03-29)


### Features

* **web:** read CLI version from pyproject.toml at build time ([097d1c0](https://github.com/deepgram/cli/commit/097d1c08e70d8426ce6037e8fd63503f17f65318))

## [0.2.6](https://github.com/deepgram/cli/compare/v0.2.5...v0.2.6) (2026-03-29)


### Features

* **web:** add favicon from dx-design lettermark-square ([7aa290c](https://github.com/deepgram/cli/commit/7aa290c7b37708f2f279dc35e14195ddf1be2f4b))


### Bug Fixes

* **ci:** trigger web production deploy via workflow_call not release event ([5913670](https://github.com/deepgram/cli/commit/59136709a4486423e1cfee2d7d6d79f3e2c0bfc6))
* **install:** expose dg/deepgram aliases and fix PATH after install ([bacc780](https://github.com/deepgram/cli/commit/bacc780dfb6823bcffd1d2965f65c40be7fb0794))
* **listen:** use markup=False when printing transcript and summary ([e87f68e](https://github.com/deepgram/cli/commit/e87f68e5fb1622e515408fac4d52ae323faf1ea3))
* replace dg transcribe with dg listen across web content and skill generator ([4676d34](https://github.com/deepgram/cli/commit/4676d34dd0d5cc5603c68fd729ff7196bf8531df))


### Documentation

* add examples section to README with captions, piping, and management ([f7fd975](https://github.com/deepgram/cli/commit/f7fd9759a6e967f5eae72920be2890bbc127e5b7))

## [0.2.5](https://github.com/deepgram/cli/compare/v0.2.4...v0.2.5) (2026-03-29)


### Features

* **cli:** add whoami, --dry-run, and shell completion ([5577ef2](https://github.com/deepgram/cli/commit/5577ef2a37049cfddf227fd14d5705607ad056d0))
* **core:** add AI agent detection and --agent-friendly flag ([a5de7e2](https://github.com/deepgram/cli/commit/a5de7e24deba1521ee838aca2264f04dd2d0a933))
* **listen:** auto-select listen.v2 for flux-* models ([9920023](https://github.com/deepgram/cli/commit/992002379ca362a0ea62161262b282177c377c6b))
* **listen:** unified STT command with captions and transcribe alias ([1c6e8d8](https://github.com/deepgram/cli/commit/1c6e8d896b826d2be538c637a64517288478b748))
* **web:** add Astro landing page for deepgram CLI ([91079df](https://github.com/deepgram/cli/commit/91079df5f59d3ade5d495a516946f6b4bae89240))
* **web:** comprehensive SEO, OG, JSON-LD, GEO, and LLM optimisation ([346625f](https://github.com/deepgram/cli/commit/346625f0fc1452e223fe7c5c2f5d981e99818054))
* **web:** randomised terminal demo with caption examples ([7ee635c](https://github.com/deepgram/cli/commit/7ee635c2ae9c2a5d2cf51839c163e9dc2ac7ebfb))
* **web:** switch install to deepgram.com/install.sh and install.ps1 ([d5e07ae](https://github.com/deepgram/cli/commit/d5e07ae2eba12f7747f6100f984c568a1c7a8b08))
* **web:** use Deepgram wordmark, simplify nav and hero install CTA ([42cd42f](https://github.com/deepgram/cli/commit/42cd42f6dadc4f37a00ded16568392981ee8b04d))


### Bug Fixes

* **ci:** fix netlify deploy — add --no-build, remove silent error swallowing ([be2ff3f](https://github.com/deepgram/cli/commit/be2ff3fa6b678ed77d2144ea3bbf1d78a71fbc4f))
* **web:** accurate terminal demo — correct model names and 14 sequences ([3b2a861](https://github.com/deepgram/cli/commit/3b2a861439a8f4c9f1e7f755c28499d47e7f4e8e))
* **web:** generate og-image.png at build time via @resvg/resvg-js ([1897c0b](https://github.com/deepgram/cli/commit/1897c0be0ce5a882846cecfbdddb068fd80be20a))

## [0.2.4](https://github.com/deepgram/cli/compare/v0.2.3...v0.2.4) (2026-03-25)


### Features

* **skills:** fetch latest skills from deepgram/skills on every install ([b498311](https://github.com/deepgram/cli/commit/b4983116361b232ecad926ed6ced84ae84f09e37))
* **skills:** interactive tool selection for skills setup ([382b7a0](https://github.com/deepgram/cli/commit/382b7a05d47548175771b9e34f1396973d9b1e77))


### Bug Fixes

* **mcp:** update default base URL to api.dx.deepgram.com ([c514545](https://github.com/deepgram/cli/commit/c514545c52622f516eb51703fa787fa0d68751ff))

## [0.2.3](https://github.com/deepgram/cli/compare/v0.2.2...v0.2.3) (2026-03-24)


### Documentation

* add community files from SDK template ([7deb2ba](https://github.com/deepgram/cli/commit/7deb2ba718ab762db4a42e0d33b5ca7747fcb807))
* add MIT license file ([9334818](https://github.com/deepgram/cli/commit/9334818339ae017735ff1e44a3a650eb57800616))
* point Code of Conduct to canonical community URL ([b1ae7f5](https://github.com/deepgram/cli/commit/b1ae7f5478163896198b6e949b8815b0d386ea27))
* replace generic CoC with official Deepgram Code of Conduct ([187eb1b](https://github.com/deepgram/cli/commit/187eb1b904314a6eabe1f92c21a70d7616fd40e6))
* rewrite README with banner image, add logo to CLI help ([8c51166](https://github.com/deepgram/cli/commit/8c511663a19633b845f4284195b48fe003be0086))

## [0.2.2](https://github.com/deepgram/cli/compare/v0.2.1...v0.2.2) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))
* **install:** add Windows installer and version/force options ([59620b2](https://github.com/deepgram/cli/commit/59620b2860dced68af027edf477bcbeb717c7b03))

## [0.2.1](https://github.com/deepgram/cli/compare/v0.2.0...v0.2.1) (2026-03-09)


### Features

* **install:** replace Homebrew with curl-pipe-sh installer ([87bd0c6](https://github.com/deepgram/cli/commit/87bd0c675452b2e8257392b8509730b9a3de34dc))

## [0.2.0](https://github.com/deepgram/cli/compare/v0.1.10...v0.2.0) (2026-03-09)


### ⚠ BREAKING CHANGES

* **core:** InstallationDetector and VersionChecker moved from deepctl_core to deepctl_cmd_update

### Features

* Add API key verification system ([0796b1e](https://github.com/deepgram/cli/commit/0796b1e01f91fd1a61db595c2a3ed7dfdbac723d))
* Add Homebrew testing infrastructure ([27e1171](https://github.com/deepgram/cli/commit/27e1171d82b8301a3bcd132904a720413ec35be1))
* add MCP server command for Gnosis AI integration ([4babddf](https://github.com/deepgram/cli/commit/4babddf09c32c2cb954c9224f500ff6bba0ac0ae))
* add models module with FileInfo model ([a0cb5aa](https://github.com/deepgram/cli/commit/a0cb5aaafaa92690d72033b7d8a79e52cea620c4))
* add performance timing system with --timing and --timing-detailed flags ([45132ff](https://github.com/deepgram/cli/commit/45132ffa8e76ce714c8ac139dc6c40e235f39516))
* Add plugin search command with hardcoded registry ([bd533cb](https://github.com/deepgram/cli/commit/bd533cb4b90b531fc522c4b2fd9a582d05d7861a))
* Add self-update functionality to deepctl ([b2152f2](https://github.com/deepgram/cli/commit/b2152f2f4f1d9cde6ac1f822dfd53ae376ae3f50))
* Add universal plugin support for all installation methods ([6c6d3c3](https://github.com/deepgram/cli/commit/6c6d3c3f87a3451c9474e8a1bcde6bd0148d4acc))
* **api,debug:** add `deepctl api` command, `debug stream` proxy, and debug improvements ([53accd5](https://github.com/deepgram/cli/commit/53accd5a30754711fbe615e9294d52c08546bbcc))
* **auth:** switch device flow from community server to dx-id OIDC provider ([2c04fc2](https://github.com/deepgram/cli/commit/2c04fc2001d114a85073180f7c57dddb91129596))
* **browser-debug:** add browser debug HTML interface ([e44a070](https://github.com/deepgram/cli/commit/e44a070c5bd1b7456c049e9febcbbdb9c1b0216c))
* **browser-debug:** add dependencies and test configuration ([6b74cd9](https://github.com/deepgram/cli/commit/6b74cd92008359eaaec26a2907f85c3e202ccdf3))
* **browser-debug:** implement browser capability models ([c2e3cbc](https://github.com/deepgram/cli/commit/c2e3cbc9226b7e40de7606834ff9c59f358ecf7d))
* **browser-debug:** implement WebSocket-based browser debug command ([d4902f8](https://github.com/deepgram/cli/commit/d4902f86af16db16ffeaaf69916495acc9377a1c))
* complete nested command architecture implementation ([f3a9945](https://github.com/deepgram/cli/commit/f3a9945d62382696047bd45b436f67e9481dd6aa))
* **core:** comprehensive architecture review and test coverage ([25a5409](https://github.com/deepgram/cli/commit/25a5409304cd3e1bbb0b8101fd717401b49ae807))
* **debug-audio:** add ffmpeg-python dependency for audio file analysis ([398b2eb](https://github.com/deepgram/cli/commit/398b2eb1791c0fcbc662842d7760228d69f6bb90))
* **debug-audio:** implement comprehensive audio file analysis ([fb773b6](https://github.com/deepgram/cli/commit/fb773b6ab27f738741ade32851df959c8a9fe29d))
* **docs:** auto-generate root README sections from pyproject.toml ([5c8cb9a](https://github.com/deepgram/cli/commit/5c8cb9a4406e7277d5e72ef27b981fc86235686f))
* Enable PyPI publishing in release workflow ([d78f2e2](https://github.com/deepgram/cli/commit/d78f2e228867f55b75cb28c8829a23f46c986eb3))
* enhance Makefile with better organization and documentation ([944d195](https://github.com/deepgram/cli/commit/944d19520fb3c11a25e25abddbdcce6ef81a1226))
* enhance performance timing and core architecture ([830aa47](https://github.com/deepgram/cli/commit/830aa47b67398e7758f31b3957c62a400ae822e9))
* enhance test infrastructure with flexible running options ([d42a5e9](https://github.com/deepgram/cli/commit/d42a5e95a1d273378c82a2648f914c3b034d8cbe))
* **ffprobe:** add audio analysis via ffprobe across CLI ([8e2c923](https://github.com/deepgram/cli/commit/8e2c92387815b08f86373c49ab6c435f9d18d846))
* Implement comprehensive network debug command ([0d6c310](https://github.com/deepgram/cli/commit/0d6c31076781c3ce50209781e9e7d5f01a48c17f))
* implement hybrid homebrew distribution strategy ([548b65f](https://github.com/deepgram/cli/commit/548b65f01d0425dfc48ab082cef1f02514c608fd))
* improve update command functionality ([4a9baa0](https://github.com/deepgram/cli/commit/4a9baa04f05010a8150a4bf3bbc3b6de6eedb39a))
* **init:** add dg init command to scaffold Deepgram starter apps ([60c17c3](https://github.com/deepgram/cli/commit/60c17c305851cdc2b2489e0f80dc4c64bebd0a0d))
* initial commit ([7365137](https://github.com/deepgram/cli/commit/7365137db9f8797d5aff3f4b5516c49670777903))
* **mcp:** fix auth, switch to streamable-http, and improve READMEs ([8e76d60](https://github.com/deepgram/cli/commit/8e76d6096ec319b5f0c85d57b299a7f05a60b5a8))
* **mcp:** rearchitect as proxy to dx-api MCP endpoint ([65a62f4](https://github.com/deepgram/cli/commit/65a62f4716a1b92d27c82a0e93d922cca522c24e))
* **plugin:** add install-aware plugin system with venv bridge and strategy pattern ([7dd849b](https://github.com/deepgram/cli/commit/7dd849b6d5cc66fc6a9d54d665aad8909830b5ef))
* setup uv workspace (monorepo) structure ([42eec32](https://github.com/deepgram/cli/commit/42eec32ae1327403e24a99ab869b6a12d4e4a411))
* **skills:** add `deepctl skills` command and agent-native CLI metadata ([5654d40](https://github.com/deepgram/cli/commit/5654d40d3a6c2caf790a9de37c17ad60c150e8d3))
* **skills:** replace CLI help dump with Deepgram Developer Guide ([4182d9b](https://github.com/deepgram/cli/commit/4182d9b20aa638ad031d9a6ed56c6f36b4aec292))
* standardize release process with Makefile commands ([79083d4](https://github.com/deepgram/cli/commit/79083d4d90ab43bb8a50432437542b8aa7bc385e))
* update authentication and core modules ([1a1df1c](https://github.com/deepgram/cli/commit/1a1df1cf0db70c902eb8c1ef3c54cc17d980605b))
* update authentication and security implementation ([44d5568](https://github.com/deepgram/cli/commit/44d5568ebc8d50bf2b6d3ed31e2248da38d2565c))
* **update:** add background plugin update notifications ([5dfb906](https://github.com/deepgram/cli/commit/5dfb90635fe2cd62992c46f44d50f615659e7ec8))
* **update:** overhaul update/upgrade system with expanded detection and startup notifications ([f71437a](https://github.com/deepgram/cli/commit/f71437a7316d3703786cedeb00ec28cc3a54928f))


### Bug Fixes

* add --find-links to release workflow for local dependency resolution ([b5f0a01](https://github.com/deepgram/cli/commit/b5f0a019b36b7d5328bb752335203b05c9adb630))
* add contents read permissions to GitHub Actions jobs ([759cd57](https://github.com/deepgram/cli/commit/759cd57817c27d5d76efc386f8b211fb6d79a8a5))
* add explicit exports to core models ([2827f9a](https://github.com/deepgram/cli/commit/2827f9a35b5cb93086ced446cdc76be7f61f300b))
* **auth:** decouple API key validation from project ID requirement ([0f67b22](https://github.com/deepgram/cli/commit/0f67b225217f2ddeb5ef29109f878fdb4d3b589d))
* **auth:** open browser and start polling immediately on login ([ecb8271](https://github.com/deepgram/cli/commit/ecb82713049b3aa348ebae8507d463ae9a063e70))
* auto-detect CI environment in publish script to avoid EOFError ([ebfe5d4](https://github.com/deepgram/cli/commit/ebfe5d4f955c05be49f0473b642550547a96d3af))
* complete pyproject.toml package configuration ([bf7d898](https://github.com/deepgram/cli/commit/bf7d8980756f1a210825e3dd0bb6162b2f1173f2))
* configure pytest to run tests across all workspace packages ([17e1ba6](https://github.com/deepgram/cli/commit/17e1ba6230894dc4122dec55f9c23f73df886a66))
* **debug-audio:** correct indentation in error handling blocks ([f8931a2](https://github.com/deepgram/cli/commit/f8931a22515fc1bd4018443ab78885e5973d21cd))
* **debug-audio:** restore correct indentation in handle method ([e40cf29](https://github.com/deepgram/cli/commit/e40cf298ece2df72b1ee2b91f723349b3be3f1fe))
* handle graceful shutdown of MCP server to prevent threading errors ([0fbb772](https://github.com/deepgram/cli/commit/0fbb772b36c08254fe948eabc92f2a75362c3929))
* improve MCP server shutdown handling to work with single Ctrl+C ([7bc1d94](https://github.com/deepgram/cli/commit/7bc1d94eee4ca83ce6d27c23a48cc1b7c5f2213a))
* make MCP server actually exit on first Ctrl+C ([4b4a245](https://github.com/deepgram/cli/commit/4b4a245e96543bf77100f93d4779681a313deed9))
* MCP server now exits cleanly on first Ctrl+C ([49c4aca](https://github.com/deepgram/cli/commit/49c4aca98812d2030c08091f804f58f0ad1d13e0))
* package verification for deepctl-cmd-plugin ([d00a792](https://github.com/deepgram/cli/commit/d00a79204a4b370128e7434d8a06827f56abd223))
* Remove duplicate entries in plugin list ([11a3fe3](https://github.com/deepgram/cli/commit/11a3fe340e320df46c3d6ed872f469205b32163f))
* resolve all ruff and mypy linting issues ([83eaa7a](https://github.com/deepgram/cli/commit/83eaa7a54093eae72e9f6f08ec021980abc2a9fd))
* resolve mypy type checking issues and modernize Python code ([a93526e](https://github.com/deepgram/cli/commit/a93526eb0a3c9eb48d068f3180a6f373c8bfa0c3))
* resolve regex SyntaxWarnings in version.py script ([373911a](https://github.com/deepgram/cli/commit/373911aa83a2ac168d23a1a2ebe06e86adaf438d))
* **skills:** fix entry point iteration in collect_command_metadata ([7b8424f](https://github.com/deepgram/cli/commit/7b8424f91e86ff10ba591d5a58333adf7a2b795f))
* skip confirmation prompt in non-interactive mode ([2bf3ac5](https://github.com/deepgram/cli/commit/2bf3ac54d844f55bac1f08d43f84571bbbd9a2bc))
* **tests:** make config tests cross-platform compatible for Windows ([701263d](https://github.com/deepgram/cli/commit/701263dacb78aa2f17e9713b56e131b776cf7821))
* **tests:** skip Unix-specific path tests on Windows ([71ee600](https://github.com/deepgram/cli/commit/71ee6004b585b072e5f4498eb7550d9243737e40))
* **tests:** update integration tests for audio command required file argument ([a12c5af](https://github.com/deepgram/cli/commit/a12c5af583c7726b5b62cdadc077d92bde03c220))
* **tests:** use real tmp_path for pipx run detection test ([7b0a8d6](https://github.com/deepgram/cli/commit/7b0a8d64bcfba11988e6321b8372c03a16cfd5b0))
* **tooling:** resolve all ruff, mypy, and Makefile issues ([3500379](https://github.com/deepgram/cli/commit/35003791a94ce74b40292dad091e5139299a620e))
* **transcribe:** fix SDK response serialization and redesign output ([908097c](https://github.com/deepgram/cli/commit/908097cfd3f1323d67eaa797bd00c037accfb629))
* Update test assertions to match actual command class names and Click behavior ([c85d756](https://github.com/deepgram/cli/commit/c85d756935d006805f89a4870b424e224839fcab))
* use absolute path for --find-links in pipx installation instructions ([bc928ac](https://github.com/deepgram/cli/commit/bc928acd9d1b9b8922f8d8e393a99101a3e269e1))


### Documentation

* add architecture documentation for MCP server and plugin system ([b65af57](https://github.com/deepgram/cli/commit/b65af57fc10edfba3ff434d8d60f8875da6f141f))
* Add comprehensive Homebrew testing guide ([0203e9e](https://github.com/deepgram/cli/commit/0203e9e8b4c8a20de1035b8a096b85d6dfc0d80c))
* add comprehensive project documentation ([ed94015](https://github.com/deepgram/cli/commit/ed9401573edbbae0ba5398cefb8a6086c33c996f))
* add comprehensive testing and test strategy documentation ([77dd5db](https://github.com/deepgram/cli/commit/77dd5dbe7d71b1a5da1ee3d39cd21dba43c90069))
* Add future plans for plugin registry URL-based system ([067b658](https://github.com/deepgram/cli/commit/067b658471f6fcbe6d420d533cf5686c816a7148))
* Add plugin search command to main README ([e1b2548](https://github.com/deepgram/cli/commit/e1b254898a75e56074c32295d82ee1b525ec408f))
* add release documentation to README ([d73dc3d](https://github.com/deepgram/cli/commit/d73dc3da875e05a2811a12a951d904332029ac52))
* add versioning strategy and PyPI publishing guides ([b1e2e9c](https://github.com/deepgram/cli/commit/b1e2e9c23d7cbc47e24d6a8472a26bd730462776))
* **browser-debug:** add comprehensive architecture documentation ([90a0873](https://github.com/deepgram/cli/commit/90a0873ed3597c894e593d2207b4be75852ce871))
* **debug-audio:** add comprehensive documentation ([6a32c2b](https://github.com/deepgram/cli/commit/6a32c2b379a5bdff4342a649d5d95aa408204aa9))
* fix critical inconsistencies between documentation and code ([6455932](https://github.com/deepgram/cli/commit/6455932c4d1e950107279a44f6070330a8b7cde6))
* improve local installation instructions in build script ([ed04659](https://github.com/deepgram/cli/commit/ed0465959a99591734346d57df1e2acc3bfcc3ca))
* prioritize pipx for global installation with plugin support ([0c17869](https://github.com/deepgram/cli/commit/0c1786919ba2424a74227b0099ebae74f29185dc))
* **readme:** document install-aware plugin system and strategies ([3f0a8cf](https://github.com/deepgram/cli/commit/3f0a8cf9fb402d97e176c06300bb4b01584c2e30))
* **readme:** regenerate READMEs for new api and debug-stream packages ([bd7170a](https://github.com/deepgram/cli/commit/bd7170af5548b36a7b0ea46fc7031503d9251837))
* reorganize documentation structure ([0e6f279](https://github.com/deepgram/cli/commit/0e6f279caf11168107f09402bbd720442c1338a8))
* simplify and restructure README for better clarity ([95aa5ec](https://github.com/deepgram/cli/commit/95aa5ec5622a09217381fbdc21f7bc9f2f54caec))
* update architecture and home documentation ([56b4650](https://github.com/deepgram/cli/commit/56b465033961ee6bc0d22da838716ae4d92f792d))
* update documentation to reflect current architecture and improve clarity ([bbf75b0](https://github.com/deepgram/cli/commit/bbf75b034d6268db891e2371f670b08578b1ef4c))
* update plugin example README ([1b825a6](https://github.com/deepgram/cli/commit/1b825a684ac6e71e01792ad7e63023e1aa293fd8))
