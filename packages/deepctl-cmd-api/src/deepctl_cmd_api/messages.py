"""User-facing strings for `dg api`, keyed by message ID.

Placeholders are filled with ``deepctl_core.messages.display`` values; a
composed line is markup-escaped before it is printed.
"""

from deepctl_core import messages as _core_msg

W_API_NOAUTH = (
    "Sending the request to {host} without your API key: it is not a Deepgram API host."
)
W_API_NOAUTH_HINT = "To include your key, pass --allow-foreign-host={host}"
E_API_MISMATCH = (
    "--allow-foreign-host={given} does not match the request host {host}. "
    "Nothing was sent."
)
E_API_MISMATCH_HINT = "If you trust {host}, pass --allow-foreign-host={host}"
E_API_AGENT = (
    "Not sending your API key to {host}: this session looks like an AI agent, "
    "so command-line overrides aren't accepted. Nothing was sent."
)
E_API_AGENT_HINT = _core_msg.E_FH_AGENT_HINT
E_API_INSECURE = (
    "Not sending your API key to {origin}: keys are sent over plain http only "
    "to a loopback host you configured or approved. Nothing was sent."
)
E_API_INSECURE_HINT = "Use an https:// URL."
W_API_REDIRECT = (
    "{origin} answered with a redirect to {location_origin}. dg api doesn't "
    "follow redirects."
)
# After a TLS failure on localhost, 127.0.0.1 or [::1] while a person has
# DEEPGRAM_CLI_INSECURE set: dg api always verifies TLS (as in 0.4.1).
H_API_INSECURE_IGNORED = (
    "DEEPGRAM_CLI_INSECURE doesn't apply to dg api, which always verifies TLS "
    "certificates."
)
