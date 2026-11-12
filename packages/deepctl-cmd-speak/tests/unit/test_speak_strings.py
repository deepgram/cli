"""Speak's messages match the error catalogue (``error_codes.json``)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from deepctl_cmd_speak import messages as msg

# (code, error ID, hint_human, hint_agent, hint_agent_if_next_step_null)
_WIRING = [
    ("output_unwritable", "E-OUTPUT-UNWRITABLE", "H-OUTPUT-UNWRITABLE",
     "A-OUTPUT-UNWRITABLE", None),
    ("invalid_argument", "E-STDOUT-BYTES", "H-STDOUT-BYTES", "H-STDOUT-BYTES", None),
    ("invalid_argument", "E-SPEAK-NO-OUTPUT", "H-SPEAK-NO-OUTPUT",
     "A-SPEAK-NO-OUTPUT", "A-SPEAK-NO-OUTPUT-NULL"),
    ("invalid_argument", "E-SPEAK-NO-TEXT", "H-SPEAK-NO-TEXT", "H-SPEAK-NO-TEXT", None),
    ("invalid_argument", "E-OUT-FILE-EXISTS", "H-OUT-FILE-EXISTS",
     "H-OUT-FILE-EXISTS", None),
    ("invalid_argument", "E-SPEAK-O-FORMAT", "H-SPEAK-O-FORMAT",
     "H-SPEAK-O-FORMAT", None),
    ("invalid_argument", "E-SPEAK-O-TEXT", "H-SPEAK-O-TEXT", "H-SPEAK-O-TEXT", None),
    ("invalid_argument", "E-SPEAK-OUT-CONFLICT", "H-SPEAK-OUT-CONFLICT",
     "H-SPEAK-OUT-CONFLICT", None),
]  # fmt: skip


def _ids() -> dict[str, str]:
    return {
        name.replace("_", "-"): value
        for name, value in vars(msg).items()
        if re.fullmatch(r"[EHAD]_[A-Z0-9_]+", name)
    }


def test_every_wired_id_has_a_string():
    ids = _ids()
    assert "A-SPEAK-NO-OUTPUT-ARGV" not in ids  # folded into the NULL variant
    for _code, *message_ids in _WIRING:
        for message_id in message_ids:
            assert message_id is None or message_id in ids, message_id


@pytest.mark.skipif(
    not os.environ.get("DG_ERROR_CODES_JSON"),
    reason="DG_ERROR_CODES_JSON names no error catalogue file",
)
def test_strings_match_catalogue():
    catalogue = json.loads(Path(os.environ["DG_ERROR_CODES_JSON"]).read_text())
    for message_id, text in _ids().items():
        assert catalogue["strings"][message_id] == text, message_id
    for code, error_id, human, agent, agent_null in _WIRING:
        entry = catalogue["codes"][code]
        message = next(m for m in entry["messages"] if m["id"] == error_id)
        assert message["hint_human"] == human, error_id
        assert message["hint_agent"] == agent, error_id
        assert message.get("hint_agent_if_next_step_null") == agent_null, error_id
    assert msg.CODE_INVALID_ARGUMENT in catalogue["codes"]
    assert msg.CODE_OUTPUT_UNWRITABLE in catalogue["codes"]
