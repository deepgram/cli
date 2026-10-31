"""User-facing strings for ``dg speak``, keyed by message ID.

Every ID and string matches the error catalogue (``error_codes.json``)
exactly; the attribute name is the ID with ``-`` as ``_``.
"""

from __future__ import annotations

# ── Catalogue ──────────────────────────────────────────────────────────────

CODE_OUTPUT_UNWRITABLE = "output_unwritable"

# output_unwritable: a write to stdout or the audio file failed.
# {reason} is details.reason: an OS error text or one of dg's own (sinks.py).
E_OUTPUT_UNWRITABLE = "Couldn't write the output to {output}: {reason}."
H_OUTPUT_UNWRITABLE = (
    "Choose another destination, or fix this one (free space, permissions), "
    "then try again."
)
A_OUTPUT_UNWRITABLE = (
    "If you chose this destination, choose another one. Otherwise tell the "
    "user {prog} couldn't write its output ({reason}), and don't retry until "
    "it's fixed."
)
