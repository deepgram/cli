# Changelog

## [0.0.5](https://github.com/deepgram/cli/compare/deepctl-cmd-speak-v0.0.4...deepctl-cmd-speak-v0.0.5) (2026-10-09)


### Features

* **speak:** add --play and --list-voices to the speak command ([59683a7](https://github.com/deepgram/cli/commit/59683a73db4ffe893fcf6f7f76c387b0136d7bbd))
* **speak:** add --play and --list-voices to the speak command ([#110](https://github.com/deepgram/cli/issues/110)) ([fde4ed3](https://github.com/deepgram/cli/commit/fde4ed38920c9bf3a22acdef361f0fc9d204e8a3))


### Bug Fixes

* **speak:** carry voice languages as data and flag the unlisted default ([d8dffc4](https://github.com/deepgram/cli/commit/d8dffc44d8100aab682e3aed5e85980ef988d7a8))
* **speak:** gate --play per player instead of treating paplay like aplay ([2dfdae2](https://github.com/deepgram/cli/commit/2dfdae25664ec7202c78d3b6b5e42492b5291a6c))
* **speak:** honor Aura's WAV default for playback ([3cf2d20](https://github.com/deepgram/cli/commit/3cf2d208e5d39ab8bf0dc28b596e9caababacf50))
* **speak:** keep --play honest about the file it saved and the empty pipe ([dafb3a9](https://github.com/deepgram/cli/commit/dafb3a9aebee710535f019c2065c10a073ef4479))
* **speak:** list voices by their canonical -m value and languages ([a8cd658](https://github.com/deepgram/cli/commit/a8cd658a1e09ee0b0f84c3d40ff3148e75598205))
* **speak:** print the errors the framework never shows, and re-order the pipe note ([9d9a3d0](https://github.com/deepgram/cli/commit/9d9a3d0a9a0120c9bbf8bbb01686ddcefdc6119e))
* **speak:** retain Aura's production MP3 default ([07d1e2b](https://github.com/deepgram/cli/commit/07d1e2bf8109092c403bcab06ac8887c6fcf77c7))
* **speak:** stop rich from eating bracketed paths out of error messages ([9bed27f](https://github.com/deepgram/cli/commit/9bed27fee88f5a2fc04015b80449ef68880c3deb))

## [0.0.4](https://github.com/deepgram/cli/compare/deepctl-cmd-speak-v0.0.3...deepctl-cmd-speak-v0.0.4) (2026-08-17)


### Features

* SDK 7.7.0 — Flux TTS controls, Flux STT fix, listen redact/numerals ([#92](https://github.com/deepgram/cli/issues/92)) ([50d96cf](https://github.com/deepgram/cli/commit/50d96cf8950c9f180619e0e2dbd41931d1a63ef6))
* **speak:** default to Flux TTS (flux-alexis-en) instead of Aura 2 ([#89](https://github.com/deepgram/cli/issues/89)) ([5a0b698](https://github.com/deepgram/cli/commit/5a0b6981755d58cb5a1725150bf437c81c792433))

## [0.0.3](https://github.com/deepgram/cli/compare/deepctl-cmd-speak-v0.0.2...deepctl-cmd-speak-v0.0.3) (2026-07-15)


### Features

* **speak:** Flux TTS (Speak v2 WebSocket streaming) ([#86](https://github.com/deepgram/cli/issues/86)) ([15526ac](https://github.com/deepgram/cli/commit/15526ac08a6b931f260223123c2cfe6b5cc08ec0))

## [0.0.2](https://github.com/deepgram/cli/compare/deepctl-cmd-speak-v0.0.1...deepctl-cmd-speak-v0.0.2) (2026-03-23)


### Features

* add 8 new commands covering full Deepgram API surface ([a034321](https://github.com/deepgram/cli/commit/a0343218bb65241c46e43556d7c67ccb472542f7))
