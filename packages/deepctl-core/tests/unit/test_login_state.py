"""The state dir helper and the pending device-login store."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from deepctl_core import login_state
from deepctl_core.login_state import (
    PendingLogin,
    load_pending,
    remove_pending,
    save_pending,
    unwritable_dir,
)

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home / ".deepctl"


def pending(**over):
    values = dict(
        device_code="dc",
        user_code="ABCD-EFGH",
        verification_uri="https://id.example/auth/cli",
        interval=6,
        expires_at=2_000_000_000.0,
        host="https://id.example",
        profile="default",
    )
    values.update(over)
    return PendingLogin(**values)


def test_state_dir_is_dot_deepctl_in_home(state):
    assert login_state.state_dir() == state


@posix
@pytest.mark.parametrize("umask", [0o022, 0o002, 0o077, 0])
def test_saved_file_is_0600_in_a_0700_dir(state, umask):
    old = os.umask(umask)
    try:
        path = save_pending(pending())
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert json.loads(path.read_text())["device_code"] == "dc"
    assert [p.name for p in state.iterdir()] == ["login-pending.json"]


def test_save_replaces_atomically(state):
    save_pending(pending(device_code="one"))
    save_pending(pending(device_code="two"))
    assert load_pending(agent=True).pending.device_code == "two"
    assert [p.name for p in state.iterdir()] == ["login-pending.json"]


@posix
def test_save_never_follows_a_planted_link(state, tmp_path):
    state.mkdir(mode=0o700)
    target = tmp_path / "victim"
    target.write_text("keep")
    (state / "login-pending.json").symlink_to(target)
    save_pending(pending())
    assert target.read_text() == "keep"
    assert not (state / "login-pending.json").is_symlink()


@posix
def test_state_dir_owned_by_someone_else_is_refused(state, monkeypatch):
    state.mkdir(mode=0o700)
    other = os.geteuid() + 1
    monkeypatch.setattr(login_state.os, "geteuid", lambda: other)
    with pytest.raises(login_state.StateDirError):
        save_pending(pending())


def test_round_trip_and_remove(state):
    save_pending(pending())
    loaded = load_pending(agent=True)
    assert loaded.pending == pending() and loaded.ignored_why is None
    remove_pending(loaded)
    assert not loaded.path.exists()


def test_remove_keeps_a_newer_file(state):
    save_pending(pending(device_code="old"))
    loaded = load_pending(agent=True)
    save_pending(pending(device_code="new"))
    remove_pending(loaded)
    assert load_pending(agent=True).pending.device_code == "new"


@pytest.mark.parametrize(
    "text",
    ["", "not json", "[]", '{"device_code": "dc"}', json.dumps({"interval": True})],
)
def test_malformed_file_is_absent(state, text):
    state.mkdir()
    (state / "login-pending.json").write_text(text)
    (state / "login-pending.json").chmod(0o600)  # any umask
    loaded = load_pending(agent=True)
    assert loaded.pending is None and loaded.ignored_why is None


@posix
@pytest.mark.parametrize(("mode", "why"), [(0o666, "writable"), (0o620, "writable")])
def test_agent_ignores_a_file_others_can_change(state, mode, why):
    path = save_pending(pending())
    os.chmod(path, mode)
    assert load_pending(agent=True).ignored_why == why
    assert load_pending(agent=False).pending == pending()


@posix
def test_agent_ignores_a_hard_linked_file(state, tmp_path):
    path = save_pending(pending())
    os.link(path, tmp_path / "other")
    assert load_pending(agent=True).ignored_why == "links"


def test_expiry(state):
    p = pending(expires_at=100.0)
    assert p.expired(now=100.0) and not p.expired(now=99.0)
    assert p.expires_in(now=40.0) == 60


@posix
def test_unwritable_dir_checks_the_nearest_existing_parent(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("root ignores directory modes")
        assert unwritable_dir(ro / "a" / "b") == ro / "a" / "b"
        assert unwritable_dir(tmp_path / "new" / "dir") is None
        f = ro / "config.yaml"
        assert unwritable_dir(ro, f) == ro
    finally:
        os.chmod(ro, 0o700)
    f.write_text("x")
    os.chmod(ro, 0o500)
    try:
        assert unwritable_dir(ro, f) is None  # rewritten in place
    finally:
        os.chmod(ro, 0o700)


def test_a_file_in_the_way_is_unwritable(tmp_path):
    (tmp_path / "file").write_text("x")
    assert unwritable_dir(tmp_path / "file" / "sub") == tmp_path / "file" / "sub"


@posix
def test_unreadable_file_of_someone_else_is_reported(state, monkeypatch):
    path = save_pending(pending())
    os.chmod(path, 0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("root reads any file")
        assert load_pending(agent=True).ignored_why is None  # our own file
        other = os.geteuid() + 1
        monkeypatch.setattr(login_state.os, "geteuid", lambda: other)
        assert load_pending(agent=True).ignored_why == "owner"
        assert load_pending(agent=False).ignored_why is None
    finally:
        os.chmod(path, 0o600)


def test_interval_below_the_floor_is_raised_on_read(state):
    save_pending(pending(interval=1))
    assert load_pending(agent=True).pending.interval == login_state.MIN_POLL_INTERVAL
