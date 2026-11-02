"""``list_profiles`` shows the key a profile has, wherever it is kept."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from deepctl_core import credentials
from deepctl_core.auth import AuthManager
from deepctl_core.config import Config

FILE_KEY = "f" * 36 + "FILE"
RING_KEY = "r" * 36 + "RING"


def _config(file_key: str | None) -> Mock:
    config = Mock(spec=Config)
    config.config_path = "/mock/path/config.yaml"
    config.profile = "default"
    config.list_profiles.return_value = ["default"]
    profile = Mock()
    profile.api_key = file_key
    profile.project_id = "proj"
    profile.base_url = "https://api.deepgram.com"
    config.get_profile.return_value = profile
    return config


def _listed(file_key: str | None, keyring_read: object) -> str | None:
    with (
        patch("deepctl_core.auth.httpx.Client"),
        patch.object(credentials, "keyring_read", keyring_read),
    ):
        profiles = AuthManager(_config(file_key)).list_profiles().profiles
    return profiles["default"].api_key


def test_key_only_in_config_is_shown_when_keyring_has_no_entry() -> None:
    # A keyring backend that answers "no entry" (Windows Credential
    # Manager with nothing stored) used to make this "Not set".
    assert _listed(FILE_KEY, lambda _name: None) == "****FILE"


def test_keyring_key_wins_over_config() -> None:
    assert _listed(FILE_KEY, lambda _name: RING_KEY) == "****RING"


@pytest.mark.parametrize(
    "error", [RuntimeError("no backend"), credentials.KeyringTimeout()]
)
def test_keyring_error_falls_back_to_config(error: BaseException) -> None:
    def failing(_name: str) -> str | None:
        raise error

    assert _listed(FILE_KEY, failing) == "****FILE"


def test_no_key_anywhere_is_none() -> None:
    assert _listed(None, lambda _name: None) is None
