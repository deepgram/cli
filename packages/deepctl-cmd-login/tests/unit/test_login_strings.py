"""Login's user-facing strings equal the error catalogue's.

Runs when DG_ERROR_CODES_JSON names the catalogue file; skipped otherwise.
"""

from __future__ import annotations

import json
import os

import pytest
from deepctl_core import messages as msg

CATALOGUE = os.environ.get("DG_ERROR_CODES_JSON")
pytestmark = pytest.mark.skipif(not CATALOGUE, reason="DG_ERROR_CODES_JSON not set")

# catalogue id -> messages.py constant
IDS = {
    "E-CONFIRM-ENVKEY": "E_CONFIRM_ENVKEY",
    "A-CONFIRM-ENVKEY": "A_CONFIRM_ENVKEY",
    "A-CONFIRM-ENVKEY-NULL": "A_CONFIRM_ENVKEY_NULL",
    "C-CONFIRM-ENVKEY-NULL": "C_CONFIRM_ENVKEY_NULL",
    "E-CONFIRM-RELOGIN": "E_CONFIRM_RELOGIN",
    "H-CONFIRM-YES": "H_CONFIRM_YES",
    "A-CONFIRM-YES": "A_CONFIRM_YES",
    "A-CONFIRM-YES-NULL": "A_CONFIRM_YES_NULL",
    "C-CONFIRM-YES-NULL": "C_CONFIRM_YES_NULL",
    "M-LOGIN-PENDING": "M_LOGIN_PENDING",
    "I-LOGIN-PENDING": "I_LOGIN_PENDING",
    "A-LOGIN-PENDING": "A_LOGIN_PENDING",
    "H-LOGIN-PENDING": "H_LOGIN_PENDING",
    "E-AUTH-PENDING": "E_AUTH_PENDING",
    "H-AUTH-PENDING": "H_AUTH_PENDING",
    "A-AUTH-PENDING": "A_AUTH_PENDING",
    "E-AUTH-EXPIRED": "E_AUTH_EXPIRED",
    "H-AUTH-EXPIRED": "H_AUTH_EXPIRED",
    "A-AUTH-EXPIRED": "A_AUTH_EXPIRED",
    "E-AUTH-DENIED": "E_AUTH_DENIED",
    "H-AUTH-DENIED": "H_AUTH_DENIED",
    "A-AUTH-DENIED": "A_AUTH_DENIED",
    "E-STATE-UNWRITABLE": "E_STATE_UNWRITABLE",
    "H-STATE-UNWRITABLE": "H_STATE_UNWRITABLE",
    "A-STATE-UNWRITABLE": "A_STATE_UNWRITABLE",
    "E-CFG-SAVE-UNTRUSTED": "E_CFG_SAVE_UNTRUSTED",
    "W-LOGIN-PENDING-UNTRUSTED": "W_LOGIN_PENDING_UNTRUSTED",
    "E-NETWORK-UNREADABLE": "E_NETWORK_UNREADABLE",
    "H-NETWORK-UNREADABLE": "H_NETWORK_UNREADABLE",
    "A-NETWORK-UNREADABLE": "A_NETWORK_UNREADABLE",
}


@pytest.fixture(scope="module")
def catalogue():
    with open(CATALOGUE, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize(("cid", "const"), sorted(IDS.items()))
def test_string_matches_the_catalogue(catalogue, cid, const):
    assert getattr(msg, const) == catalogue["strings"][cid]


def test_codes_and_details_keys(catalogue):
    codes = catalogue["codes"]
    used = {
        msg.CODE_CONFIRMATION_REQUIRED: {"flag"},
        msg.CODE_AUTH_PENDING: {"retry_after", "verification_uri", "user_code"},
        msg.CODE_AUTH_EXPIRED: set(),
        msg.CODE_AUTH_DENIED: set(),
        msg.CODE_STATE_UNWRITABLE: {"dir", "reason", "path"},
        msg.CODE_NETWORK_UNAVAILABLE: {"host", "reason"},
    }
    for code, keys in used.items():
        assert code in codes
        assert keys <= set(codes[code].get("details") or [])
    reasons = codes["state_unwritable"]["details_values"]["E-CFG-SAVE-UNTRUSTED"]
    assert reasons["reason"] == ["untrusted_config"]
    unreadable = codes["network_unavailable"]["details_values"]["E-NETWORK-UNREADABLE"]
    assert unreadable["reason"] == ["unreadable response"]
