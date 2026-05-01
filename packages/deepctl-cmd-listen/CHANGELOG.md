# Changelog

## [0.0.10](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.9...deepctl-cmd-listen-v0.0.10) (2026-05-01)


### Features

* **deepctl-core:** use dg alias in plugin venv ABI mismatch warning ([#47](https://github.com/deepgram/cli/issues/47)) ([c7c61c0](https://github.com/deepgram/cli/commit/c7c61c04cb17889b9fbbb2d65305e685d9cb380e))

## [0.0.9](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.8...deepctl-cmd-listen-v0.0.9) (2026-03-31)


### Bug Fixes

* **listen:** emit metadata as WebVTT NOTE block in header, suppress status lines during caption streams ([58808e2](https://github.com/deepgram/cli/commit/58808e282090716aa0f808fa63bce82aa872813b))
* **listen:** show captions before JSON output path so --webvtt and --srt work on pre-recorded files ([16b4445](https://github.com/deepgram/cli/commit/16b44457fb7e1a4bbc5e8452358ab1029712ee57))
* **listen:** suppress JSON summary after live caption streams (--mic/stdin --webvtt/--srt) ([27c29e7](https://github.com/deepgram/cli/commit/27c29e759f44fb6055045a37800c099ea4f5cd8c))

## [0.0.8](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.7...deepctl-cmd-listen-v0.0.8) (2026-03-31)


### Bug Fixes

* **listen:** make sounddevice a hard dep and surface mic errors ([6b3b088](https://github.com/deepgram/cli/commit/6b3b0882ff28bf9959ee7c3e3dcd1f9b8ad41dcc))

## [0.0.7](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.6...deepctl-cmd-listen-v0.0.7) (2026-03-30)


### Bug Fixes

* **listen:** fix topics parameter name and add sentiment display ([8974b6c](https://github.com/deepgram/cli/commit/8974b6c9e6029b6da4bc0f109afaa4d5f7272c8f))

## [0.0.6](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.5...deepctl-cmd-listen-v0.0.6) (2026-03-30)


### Bug Fixes

* **listen:** fix three root causes of missing transcript output ([274aa41](https://github.com/deepgram/cli/commit/274aa41476dfce90c05f08550e3db71800ae19e3))

## [0.0.5](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.4...deepctl-cmd-listen-v0.0.5) (2026-03-30)


### Features

* **init:** prereq checking, search-and-select picker, make init integration ([7de0189](https://github.com/deepgram/cli/commit/7de018916001a28962bee5040c5175a216458264))


### Bug Fixes

* **listen:** handle Pydantic model_dump() None values in transcript extraction ([39229b0](https://github.com/deepgram/cli/commit/39229b03b1fc2bedf38701bd3c467a0d5ef42b25))

## [0.0.4](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.3...deepctl-cmd-listen-v0.0.4) (2026-03-29)


### Bug Fixes

* **listen:** use markup=False when printing transcript and summary ([e87f68e](https://github.com/deepgram/cli/commit/e87f68e5fb1622e515408fac4d52ae323faf1ea3))

## [0.0.3](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.2...deepctl-cmd-listen-v0.0.3) (2026-03-29)


### Features

* **listen:** auto-select listen.v2 for flux-* models ([9920023](https://github.com/deepgram/cli/commit/992002379ca362a0ea62161262b282177c377c6b))
* **listen:** unified STT command with captions and transcribe alias ([1c6e8d8](https://github.com/deepgram/cli/commit/1c6e8d896b826d2be538c637a64517288478b748))

## [0.0.2](https://github.com/deepgram/cli/compare/deepctl-cmd-listen-v0.0.1...deepctl-cmd-listen-v0.0.2) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))
