"""User-facing strings for ``dg speak``, keyed by message ID.

Every ID and string matches the error catalogue (``error_codes.json``)
exactly; the attribute name is the ID with ``-`` as ``_``.
"""

from __future__ import annotations

# ── Catalogue ──────────────────────────────────────────────────────────────

CODE_INVALID_ARGUMENT = "invalid_argument"
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

# invalid_argument: --out-file is stdout and -o json/yaml was asked for.
# {format} is JSON or YAML.
E_STDOUT_BYTES = (
    "{prog} {command_path} would write the audio to stdout, so there's no room "
    "for the {format} result."
)
H_STDOUT_BYTES = (
    "Give --out-file a different file from stdout, or don't ask for {format} output."
)

# invalid_argument: --out-file names an existing file and --force is absent.
E_OUT_FILE_EXISTS = "{out_file} already exists; {prog} won't overwrite it."
H_OUT_FILE_EXISTS = "Choose a new file name, or pass --force to overwrite."

# invalid_argument: speak -o given a bare output format.
E_SPEAK_O_FORMAT = (
    "{prog} speak -o is the audio file name, so -o {value} would write a file "
    "named {value}."
)
H_SPEAK_O_FORMAT = (
    "To choose the output format, put -o before the command: "
    "{prog} -o {value} speak TEXT --out-file FILE."
)

# invalid_argument: speak -o text, which isn't a root format either.
E_SPEAK_O_TEXT = "{prog} speak -o text would write the audio to a file named text."
H_SPEAK_O_TEXT = (
    "Pass --out-file FILE to save the audio. There's no text output format."
)

# invalid_argument: both -o/--output and --out-file.
E_SPEAK_OUT_CONFLICT = (
    "Use either --out-file or -o/--output for the audio file, not both."
)
H_SPEAK_OUT_CONFLICT = "Keep --out-file FILE and drop -o."

# Deprecation notice for speak -o/--output FILE. Printed once per
# process, on stderr, even under --quiet.
D_SPEAK_OUTPUT = (
    "{prog} speak -o/--output FILE will set the output format in 1.0. "
    "Use --out-file FILE instead (add --force to overwrite, as -o does)."
)

# ── Legacy texts (0.4.x), now naming --out-file ────────────────────────────

NO_OUTPUT_TTY = (
    "No output specified. Use --out-file to save to a file, "
    "--play to hear it, or pipe stdout."
)
PLAY_STDOUT_EMPTY = (
    "--play sends the audio to your player, so stdout stays empty. "
    "Add --out-file to save a file too."
)
