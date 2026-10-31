"""User-facing strings for ``dg listen``'s live path, keyed by message ID.

Every ID and string matches the error catalogue (``error_codes.json``)
exactly; the attribute name is the ID with ``-`` as ``_``.
"""

from __future__ import annotations

# ── Catalogue ──────────────────────────────────────────────────────────────

CODE_INVALID_ARGUMENT = "invalid_argument"
CODE_STREAM_DRAIN_TIMEOUT = "stream_drain_timeout"
CODE_SERVER_ERROR = "server_error"
CODE_NETWORK_UNAVAILABLE = "network_unavailable"

# invalid_argument: a hard agent signal, no SOURCE, no --mic.
E_LISTEN_NO_SOURCE = (
    "{prog} listen needs a file, a URL, --mic, or - to read audio from stdin."
)
H_LISTEN_NO_SOURCE = (
    "Pass a file path or URL. To stream audio from stdin, pass - explicitly."
)

# invalid_argument: reading stdin failed.
E_LISTEN_STDIN_UNREADABLE = "Couldn't read audio from stdin: {os_error}."
H_LISTEN_STDIN_UNREADABLE = (
    "Pass a file path or URL instead, or make sure stdin is an open pipe "
    "or file with audio in it."
)
A_LISTEN_STDIN_UNREADABLE = (
    "Pass the audio file path or URL as SOURCE instead of reading stdin, then retry."
)

# stream_drain_timeout: end of input, then the idle deadline or the cap.
E_DRAIN_TIMEOUT = (
    "The server sent nothing for {limit_s} s after the audio ended, "
    "so results may be incomplete."
)
E_DRAIN_CAP = (
    "The server was still sending results {limit_s} s after the audio ended, "
    "so results may be incomplete."
)
H_DRAIN_TIMEOUT = "Results received so far were kept. Try again if you need the rest."

# server_error / network_unavailable hints (the error text is the stream's own).
H_SERVER = (
    "Try again in a moment. If it keeps failing, contact Deepgram support "
    "with the request ID in details."
)
A_SERVER = (
    "Retry once after a few seconds. If it fails again, tell the user and "
    "include details.request_id."
)
H_NETWORK = "Check your network connection and proxy settings, then try again."
A_NETWORK = (
    "{prog} can't reach Deepgram from this environment. If you run in a "
    "sandbox, ask the user to allow network access for {prog}."
)

# ── Live-stream messages ───────────────────────────────────────────────────

# Ctrl-C or SIGTERM: the server didn't finish within the 1 s drain.
W_LISTEN_STOP_INCOMPLETE = (
    "The server hadn't sent its last results after 1 s, "
    "so the transcript may be incomplete."
)

# Microphone: audio dropped because sending fell behind. Once per stream.
W_MIC_DROPPED = (
    "Dropped {seconds} s of microphone audio because the connection couldn't keep up."
)

# server_error: the server closed the stream with an error close code.
E_LISTEN_SERVER_CLOSED = (
    "The server ended the stream with an error ({close_code}): {close_reason}"
)
E_LISTEN_SERVER_CLOSED_NO_REASON = (
    "The server ended the stream with an error ({close_code})."
)

# server_error: Flux (listen v2) reported an error in an Error frame.
E_LISTEN_FLUX_ERROR = "Flux STT error ({flux_code}): {description}"

# network_unavailable: the connection dropped without a closing handshake.
E_LISTEN_CONNECTION_LOST = "The connection to the server was lost."
