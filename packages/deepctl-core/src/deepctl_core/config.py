"""Cross-platform configuration management for deepctl.

Three layers are read: the user config file, the project ``deepgram.yaml``
in the current directory, and ``DEEPGRAM_*`` environment variables (plus
the root ``--base-url`` flag). Only the user file is ever written, and only
with the values a command explicitly changed. The project file is read
through an allowlist: it can choose output settings and a regional API URL,
nothing that touches credentials, trust, tools or telemetry.
"""

from __future__ import annotations

import atexit
import copy
import errno
import hashlib
import io
import os
import re
import stat
import sys
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs
import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from . import messages as msg
from .hosts import (
    DEFAULT_BASE_URL,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_FLAG,
    SOURCE_PROJECT,
    SOURCE_USER,
    canonical_url,
    is_regional_base_url,
    parse_origin,
)


class ProfileConfig(BaseModel):
    """Configuration for a specific profile."""

    api_key: str | None = None
    project_id: str | None = None
    base_url: str = DEFAULT_BASE_URL
    dg_token_expires_at: str | None = None


class OutputConfig(BaseModel):
    """Output formatting configuration."""

    format: str = Field(default="json", pattern="^(json|yaml|table|csv)$")
    color: bool = True
    quiet: bool = False
    verbose: bool = False


class PluginConfig(BaseModel):
    """Plugin configuration."""

    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class UpdateConfig(BaseModel):
    """Update configuration."""

    check_enabled: bool = True
    check_frequency: str = "daily"  # daily, weekly, never
    last_check: str | None = None
    installation_method: str | None = None
    installation_path: str | None = None
    cached_version_info: dict[str, Any] | None = None


class ToolsConfig(BaseModel):
    """External tools configuration."""

    ffprobe_path: str | None = None


class TelemetryConfig(BaseModel):
    """Telemetry setting, still accepted. This version sends no telemetry."""

    enabled: bool = True


class ApiConfig(BaseModel):
    """API host trust configuration. Read from the user config file only."""

    allowed_hosts: list[str] = Field(default_factory=list)


class DeepgramConfig(BaseModel):
    """Main configuration model."""

    default_profile: str = "default"
    active_profile: str | None = None  # Currently selected profile
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    output: OutputConfig = Field(default_factory=OutputConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


# Notices raised while loading config are queued, because config loads
# before the output mode (quiet, agentic) is set up. BaseCommand flushes
# them; anything still queued is flushed at exit. Deduped per process.
_pending_notices: list[tuple[str, str, str | None, bool]] = []
_seen_notices: set[tuple[str, str]] = set()
_flush_registered = False
_notices_lock = threading.Lock()


def _queue_notice(
    message_id: str, key: str, text: str, hint: str | None, *, quiet_ok: bool
) -> None:
    global _flush_registered
    with _notices_lock:
        if (message_id, key) in _seen_notices:
            return
        _seen_notices.add((message_id, key))
        _pending_notices.append((message_id, text, hint, quiet_ok))
        if not _flush_registered:
            _flush_registered = True
            atexit.register(flush_config_notices)


def flush_config_notices() -> None:
    """Print queued config notices to stderr.

    Security warnings and errors always print; ``I-*`` info notices respect
    ``--quiet``.
    """
    from rich.markup import escape

    from . import output

    pending: str | None = None
    while True:
        with _notices_lock:
            item = _pending_notices.pop(0) if _pending_notices else None
        if item is None:
            break
        message_id, text, hint, quiet_ok = item
        try:
            if (
                message_id.startswith("I-")
                and quiet_ok
                and output._output_config["quiet"]
            ):
                continue
            if pending is not None and hint != pending:
                output.print_hint(escape(pending))
            pending = None
            if message_id.startswith("I-"):
                output.print_info(escape(text), stderr=True)
            else:
                output.print_security_warning(escape(text))
            pending = hint
        except (OSError, ValueError):
            pass
    if pending is not None:
        with suppress(OSError, ValueError):
            output.print_hint(escape(pending))


# Project-file sections that are refused outright, with their message IDs.
_PROJECT_FORBIDDEN_SECTIONS = {
    "api": "W-PF-API",
    "tools": "W-PF-TOOLS",
    "telemetry": "W-PF-TELEMETRY",
    "update": "W-PF-UPDATE",
    "plugins": "W-PF-PLUGINS",
}
_PROJECT_FORBIDDEN_PROFILE_KEYS = {
    "api_key": "W-PF-APIKEY",
    "dg_token_expires_at": "W-PF-APIKEY",
    "project_id": "W-PF-PROJECT",
}
_PROJECT_FORBIDDEN_TOP_KEYS = {
    "api_key": "W-PF-APIKEY",
    "project_id": "W-PF-PROJECT",
    "active_profile": "W-PF-PROFILE",
    "default_profile": "W-PF-PROFILE",
}


def _default_config_file() -> Path:
    """The user config file at its default location (no side effects)."""
    return Path(platformdirs.user_config_dir("deepctl", "deepgram")) / "config.yaml"


def default_config_file() -> Path:
    """The user config file at its default location: the only file that can
    approve API hosts, so every hint names it."""
    return _default_config_file()


def _real_home() -> str | None:
    """The account's home directory from the OS, never from ``$HOME``."""
    if sys.platform == "win32":
        return _windows_folder(0x28)  # CSIDL_PROFILE
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir or None
    except (ImportError, KeyError, OSError):
        return None


def _windows_folder(csidl: int) -> str | None:
    """A shell folder from the OS (SHGetFolderPathW), not the environment."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        if shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            return buf.value or None
    except Exception:
        return None
    return None


def _real_default_config_file() -> Path | None:
    """Where platformdirs puts the user config with no ``HOME`` or
    ``XDG_CONFIG_HOME`` override: derived from the account itself."""
    if sys.platform == "win32":
        base = _windows_folder(0x1C)  # CSIDL_LOCAL_APPDATA
        if base is None:
            return None
        return Path(base) / "deepgram" / "deepctl" / "config.yaml"
    home = _real_home()
    if home is None:
        return None
    if sys.platform == "darwin":
        return (
            Path(home) / "Library" / "Application Support" / "deepctl" / "config.yaml"
        )
    return Path(home) / ".config" / "deepctl" / "config.yaml"


def _trust_root_location() -> tuple[Path, Path] | None:
    """``(real home, config file)`` for the one file that may widen trust in
    an AI agent session: the default location derived from the account's
    home directory (``HOME`` and ``XDG_CONFIG_HOME`` aside)."""
    real = _real_default_config_file()
    # Windows: the config lives under %LOCALAPPDATA%, which can be
    # redirected outside the profile; walk down from that folder instead.
    home = _windows_folder(0x1C) if sys.platform == "win32" else _real_home()
    if home is None or real is None:
        return None
    return Path(home), real


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _is_reparse_point(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


# Why the trust root was refused (keys of msg.W_CFG_UNTRUSTED_WHY).
_WHY_SYMLINK = "symlink"
_WHY_LINKS = "links"
_WHY_WRITABLE = "writable"
_WHY_OWNER = "owner"
_WHY_NOT_REGULAR = "not_regular"
_WHY_UNREADABLE = "unreadable"


def _open_trust_root() -> tuple[os.stat_result, Any] | None:
    """``(fstat, data)`` of the agent-session trust root, or ``None``.

    A real config that can't be parsed is ``None`` too: it can't widen
    trust, and it never stops a ``--config`` file from loading.
    """
    root = _open_trust_root_text()
    return (root[0], root[1]) if root is not None else None


def _open_trust_root_text() -> tuple[os.stat_result, Any, str | None] | None:
    """Like _open_trust_root, also returning the text the data came from."""
    try:
        st, data, why, text = _check_trust_root()
    except (ValueError, yaml.YAMLError, OSError, RecursionError):
        return None  # UnicodeDecodeError and ConfigTooLarge are ValueErrors
    return (st, data, text) if st is not None and why is None else None


def _first_leaves(data: Any, prefix: tuple[str, ...], limit: int) -> list[str]:
    """Up to ``limit`` dotted leaf paths under ``data``, depth first.

    Stops as soon as it has them, so an aliased structure is never walked
    in full.
    """
    leaves: list[str] = []
    stack: list[tuple[Any, tuple[str, ...]]] = [(data, prefix)]
    while stack and len(leaves) < limit:
        item, path = stack.pop()
        if isinstance(item, dict) and item:
            keys = [key for _, key in zip(range(limit), item, strict=False)]
            for key in reversed(keys):
                stack.append((item[key], (*path, str(key))))
        else:
            leaves.append(".".join(path))
    return leaves


def _check_trust_root() -> tuple[os.stat_result | None, Any, str | None, str | None]:
    """Open the agent-session trust root once and parse it from that fd.

    Every path component from the real home down to the file must be a
    plain directory (no symlink, no reparse point), the file is opened with
    O_NOFOLLOW and O_NONBLOCK, and the open file must be a regular file
    owned by this user, with one link and no group or other write bit.
    Returns ``(fstat, data, why, text)``: ``why`` is None when every check
    passed, otherwise one of the _WHY_* reasons (``fstat``/``data`` may then
    be None). ``text`` is what ``data`` was parsed from, or None.
    """
    location = _trust_root_location()
    if location is None:
        return None, None, _WHY_UNREADABLE, None
    home, real = location
    try:
        parts = real.relative_to(home).parts
    except ValueError:
        return None, None, _WHY_UNREADABLE, None
    if not parts:
        return None, None, _WHY_UNREADABLE, None
    try:
        if (
            sys.platform == "win32"
            or not _O_NOFOLLOW
            or os.open not in os.supports_dir_fd
        ):
            return _check_trust_root_by_path(home, parts)
        dir_fd = os.open(home, os.O_RDONLY | _O_DIRECTORY)
        try:
            for name in parts[:-1]:
                next_fd = os.open(
                    name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=dir_fd
                )
                os.close(dir_fd)
                dir_fd = next_fd
            fd = os.open(
                parts[-1], os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=dir_fd
            )
        finally:
            os.close(dir_fd)
    except FileNotFoundError:
        return None, None, _WHY_UNREADABLE, None
    except NotImplementedError:
        return _check_trust_root_by_path(home, parts)
    except OSError as exc:
        # ELOOP (the file is a symlink) or ENOTDIR (a directory is one).
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)):
            return None, None, _WHY_SYMLINK, None
        return None, None, _WHY_UNREADABLE, None
    return _checked_parse(fd)


def _check_trust_root_by_path(
    home: Path, parts: tuple[str, ...]
) -> tuple[os.stat_result | None, Any, str | None, str | None]:
    """Windows (no dir_fd): lstat every component for symlinks and reparse
    points, open the file, then lstat the components again and require the
    same identities, so a junction swapped in meanwhile is caught."""

    def walk() -> list[tuple[int, int]] | None:
        ids = []
        current = home
        for name in parts:
            current = current / name
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode) or _is_reparse_point(st):
                return None
            ids.append((st.st_dev, st.st_ino))
        return ids

    try:
        before = walk()
        if before is None:
            return None, None, _WHY_SYMLINK, None
        fd = os.open(home.joinpath(*parts), os.O_RDONLY | _O_BINARY)
    except FileNotFoundError:
        return None, None, _WHY_UNREADABLE, None
    except OSError:
        return None, None, _WHY_UNREADABLE, None
    result = _checked_parse(fd)
    try:
        after = walk()
    except OSError:
        after = None
    st = result[0]
    if after != before or (st is not None and (st.st_dev, st.st_ino) != before[-1]):
        return None, None, _WHY_SYMLINK, None
    return result


def _checked_parse(
    fd: int,
) -> tuple[os.stat_result | None, Any, str | None, str | None]:
    st = os.fstat(fd)
    if not _is_regular_fd(fd, st):
        return st, None, _WHY_NOT_REGULAR, None
    with os.fdopen(fd, "rb") as f:
        data, text = _parse_fd_text(f)
        if st.st_nlink != 1:
            return st, data, _WHY_LINKS, text
        if sys.platform != "win32":
            if st.st_uid != os.getuid():
                return st, data, _WHY_OWNER, text
            if stat.S_IMODE(st.st_mode) & 0o022:
                return st, data, _WHY_WRITABLE, text
        return st, data, None, text


def _read_yaml_fd(path: Path) -> tuple[os.stat_result | None, Any]:
    """Open ``path`` once (non-blocking) and return its identity (fstat) and,
    for a regular file, the YAML parsed from that same open file."""
    st, data, _ = _read_yaml_text_fd(path)
    return st, data


def _read_yaml_text_fd(
    path: Path,
) -> tuple[os.stat_result | None, Any, str | None]:
    """Like _read_yaml_fd, also returning the text the data came from."""
    try:
        fd = os.open(path, os.O_RDONLY | _O_NONBLOCK | _O_BINARY)
    except FileNotFoundError:
        return None, None, None
    st = os.fstat(fd)
    if not _is_regular_fd(fd, st):
        # a FIFO, device or directory: never read (or wait on) it
        return st, None, None
    with os.fdopen(fd, "rb") as f:
        data, text = _parse_fd_text(f)
        return st, data, text


def _untrusted_reason(why: str | None) -> str:
    """The W-CFG-UNTRUSTED reason for the real config, naming the cause."""
    if why is None or why not in msg.W_CFG_UNTRUSTED_WHY:
        return msg.W_CFG_UNTRUSTED
    return msg.W_CFG_UNTRUSTED_FILE.format(why=msg.W_CFG_UNTRUSTED_WHY[why])


def _same_inode(a: os.stat_result, b: os.stat_result) -> bool:
    # Some Windows file systems report index 0: never treat that as a match.
    return a.st_ino != 0 and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _same_file(a: Path, b: Path) -> bool:
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, ValueError):
        return False


def _exists(path: Path) -> bool:
    """Path.exists() that treats an unreadable location (e.g. a working
    directory another user owns) as absent instead of failing."""
    try:
        return path.exists()
    except OSError:
        return False


def _private_mkdir(path: Path) -> None:
    """Create ``path`` (and parents) with the leaf directory at 0700."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        return
    with suppress(OSError):
        os.chmod(path, 0o700)


def _tighten_dir(path: Path) -> None:
    """Restrict an existing config directory we own to 0700 (POSIX)."""
    if sys.platform == "win32" or os.path.islink(path):
        return  # never chmod through a symlinked directory
    with suppress(OSError):
        st = os.stat(path)
        if st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & 0o077:
            os.chmod(path, 0o700)


def _replace(tmp: Path, target: Path) -> None:
    """os.replace, retried briefly on Windows while another process (a
    concurrent dg, an antivirus scanner) holds the target open."""
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 4:
                raise
            time.sleep(0.05)


def _fsync_dir(path: Path) -> None:
    """Make the rename durable (POSIX)."""
    if sys.platform == "win32":
        return
    with suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# Windows opens os.open() files in text mode unless told otherwise.
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


# How long a save waits for another dg process's lock before giving up.
_LOCK_TIMEOUT = 5.0
_LOCK_STEP = 0.05


class _ConfigBusy(OSError):
    """Another process held the config lock for the whole wait."""


class _NotRegularFile(OSError):
    """The config path leads to a device, FIFO or socket: never replaced."""


_E_NOT_REGULAR = "this config file isn't a regular file, so nothing was saved"
_E_WOULD_NOT_LOAD = "this config would be too large to load, so nothing was saved"
_E_READ_BUSY = "another process is writing it"
_E_SAVE_BUSY = (
    "another process is writing this config, so nothing was saved. "
    "Run the command again when it finishes"
)


def _flock_bounded(fd: int) -> bool:
    """Take an exclusive flock on ``fd``, waiting about ``_LOCK_TIMEOUT`` s.

    False when another process held it the whole time. True when it was
    taken, and also where flock doesn't exist or doesn't work (some network
    file systems): the write then goes ahead unlocked.
    """
    try:
        import fcntl
    except ImportError:
        return True
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_LOCK_STEP)


# Limits for any config file: dg's own files are a few dozen nodes and
# under 4 KiB.
_MAX_CONFIG_NODES = 10_000
_MAX_CONFIG_DEPTH = 32
_MAX_CONFIG_BYTES = 64 << 10
# String content with aliases expanded: four times the file cap. A file
# with no aliases can't exceed 1x; 4x leaves room for a person's anchors
# and stays small enough to dump, print or save quickly.
_MAX_CONFIG_TEXT = 4 * _MAX_CONFIG_BYTES


class ConfigTooLarge(ValueError):
    """A config file with more nodes (aliases expanded) or nesting than any
    real config has, such as a YAML alias bomb."""


def _check_config_size(data: Any) -> None:
    """Raise ConfigTooLarge past the node, depth or string-size limit.

    PyYAML builds an alias as a shared reference, so loading a bomb is
    cheap; walking it, or writing it out, is not. This walk visits mapping
    keys and values and the members of lists, tuples (!!omap, !!pairs) and
    sets (!!set), aliases expanded (a save writes each alias out in full),
    counting every node and the length of every str or bytes, and stops at
    a limit, so it is bounded too.
    """
    stack: list[tuple[Any, int]] = [(data, 0)]
    nodes = 0
    text = _text_size(data)
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            text += sum(_text_size(key) for key in item)
            children: Any = item.values()
        elif isinstance(item, (list, tuple, set, frozenset)):
            # !!omap and !!pairs load as tuples, !!set as a set
            children = item
        else:
            continue
        if depth >= _MAX_CONFIG_DEPTH:
            raise ConfigTooLarge("config nested too deeply")
        for child in children:
            nodes += 1
            text += _text_size(child)
            if nodes > _MAX_CONFIG_NODES or text > _MAX_CONFIG_TEXT:
                raise ConfigTooLarge("config too large")
            stack.append((child, depth + 1))
        if text > _MAX_CONFIG_TEXT:
            raise ConfigTooLarge("config too large")


def _text_size(value: Any) -> int:
    return len(value) if isinstance(value, (str, bytes)) else 0


def _loads_back(text: str) -> bool:
    """True when dg would load ``text``, the exact YAML a save is about to
    write, again: the byte cap and every limit the loader applies. The
    dumper writes some data in a bigger form than it was read in (merged
    keys in full, hex numbers as decimal), so a file that loaded can dump
    to one that wouldn't."""
    if len(text.encode("utf-8")) > _MAX_CONFIG_BYTES:
        return False
    try:
        _yaml_from_text(text)
    except (ValueError, yaml.YAMLError):  # never echoed: no content
        return False
    return True


_MERGE_TAG = "tag:yaml.org,2002:merge"
_INT_TAG = "tag:yaml.org,2002:int"
_FLOAT_TAG = "tag:yaml.org,2002:float"
# YAML 1.1 numbers without the sexagesimal (base 60) forms, which YAML 1.2
# dropped: "1:30" stays a string, and a long "59:59:…" can't cost the
# quadratic big-int folding PyYAML does for them.
_INT_PATTERN = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
    |[-+]?0[0-7_]+
    |[-+]?(?:0|[1-9][0-9_]*)
    |[-+]?0x[0-9a-fA-F_]+)$""",
    re.X,
)
_FLOAT_PATTERN = re.compile(
    r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
    |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
    |[-+]?\.(?:inf|Inf|INF)
    |\.(?:nan|NaN|NAN))$""",
    re.X,
)
# The longest number a config may hold, in characters.
_MAX_NUMBER_CHARS = 64


class _ConfigLoader(yaml.SafeLoader):
    """SafeLoader for config files, bounded while it loads.

    - Merge keys (``<<``): every merged entry in the document counts against
      one budget. Each merged mapping is flattened first (its own merges
      resolved) and charged at that full size before PyYAML copies it, so
      neither one mapping merging many aliases, nor many mappings each
      merging one, nor merges nested inside merges can grow past the node
      limit.
    - Nesting and size: the composer refuses a node more than 32
      collections deep, flow or block, before the scanner reads any deeper,
      and stops after 10,000 nodes.
    - Numbers: no sexagesimal forms, and at most 64 characters.
    """

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._merged = 0
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        self._nodes += 1
        if self._nodes > _MAX_CONFIG_NODES + 1:  # the root, plus the limit
            raise ConfigTooLarge("config too large")
        self._depth += 1
        try:
            # 32 nested collections, plus the scalar inside the last one.
            if self._depth > _MAX_CONFIG_DEPTH + 1:
                raise ConfigTooLarge("config nested too deeply")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        for key_node, value_node in node.value:
            if key_node.tag != _MERGE_TAG:
                continue
            if isinstance(value_node, yaml.SequenceNode):
                merged = value_node.value
            else:
                merged = [value_node]
            for sub in merged:
                if isinstance(sub, yaml.MappingNode):
                    # Charge what will be copied: its own merges first.
                    self.flatten_mapping(sub)
                    self._merged += len(sub.value)
                    if self._merged > _MAX_CONFIG_NODES:
                        raise ConfigTooLarge("config too large")
        super().flatten_mapping(node)  # nested merges come back through here

    def construct_yaml_int(self, node: Any) -> Any:
        if len(self.construct_scalar(node)) > _MAX_NUMBER_CHARS:
            raise ConfigTooLarge("config number too long")
        return super().construct_yaml_int(node)

    def construct_yaml_float(self, node: Any) -> Any:
        if len(self.construct_scalar(node)) > _MAX_NUMBER_CHARS:
            raise ConfigTooLarge("config number too long")
        return super().construct_yaml_float(node)


# A copied resolver table for this loader only; yaml.SafeLoader is untouched.
_ConfigLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp) for tag, regexp in resolvers if tag not in (_INT_TAG, _FLOAT_TAG)
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ConfigLoader.add_implicit_resolver(_INT_TAG, _INT_PATTERN, list("-+0123456789"))
_ConfigLoader.add_implicit_resolver(_FLOAT_TAG, _FLOAT_PATTERN, list("-+0123456789."))
_ConfigLoader.add_constructor(_INT_TAG, _ConfigLoader.construct_yaml_int)
_ConfigLoader.add_constructor(_FLOAT_TAG, _ConfigLoader.construct_yaml_float)


# Parses of config files, per process: dg builds several Config objects per
# command. The file (at most 64 KiB) is read every time and the key is the
# SHA-256 of those bytes, so a cached result always matches the content
# read; trust is still decided from the fstat of the same fd. A refusal is
# kept too, so a bad file costs one parse.
_parse_cache: dict[bytes, tuple[bool, Any]] = {}


def _parse_fd_cached(f: Any) -> Any:
    """Parse the open config file ``f`` (bytes), at most once per content.

    Trust is the caller's: it decides from the fstat of the same fd.
    """
    return _parse_fd_text(f)[0]


def _parse_fd_text(f: Any) -> tuple[Any, str]:
    """Like _parse_fd_cached, also returning the text the data came from."""
    raw = _read_capped(f)
    key = hashlib.sha256(raw).digest()
    if key not in _parse_cache:
        result: tuple[bool, Any]
        try:
            result = (True, _yaml_from_text(raw.decode("utf-8")))
        except (ValueError, yaml.YAMLError) as exc:  # incl. ConfigTooLarge
            # without the traceback, which holds the raw text in its frames
            result = (False, exc.with_traceback(None))
        if len(_parse_cache) >= 16:
            _parse_cache.clear()
        _parse_cache[key] = result
    ok, value = _parse_cache[key]
    if not ok:
        raise copy.copy(value).with_traceback(None)  # a fresh instance each time
    return copy.deepcopy(value), raw.decode("utf-8")


class _ConfigReader:
    """An open config file whose YAML ``load()`` parses once per process."""

    def __init__(self, f: Any) -> None:
        self._f = f
        # The text the last load() parsed.
        self.text: str | None = None

    def load(self) -> Any:
        data, self.text = _parse_fd_text(self._f)
        return data


@contextmanager
def _open_config(path: Path) -> Iterator[_ConfigReader]:
    """Open a config file without blocking (a FIFO never hangs dg), for
    regular files only; it is read as bytes, capped, then decoded as UTF-8."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _O_BINARY)
    st = os.fstat(fd)
    if not _is_regular_fd(fd, st):
        raise _NotRegularFile(errno.EINVAL, "not a regular file", str(path))
    with os.fdopen(fd, "rb") as f:
        yield _ConfigReader(f)


def _is_regular_fd(fd: int, st: os.stat_result | None = None) -> bool:
    """True for a regular file. Otherwise ``fd`` is closed: os.fdopen fails
    on a directory and would leave it open."""
    try:
        mode = (st if st is not None else os.fstat(fd)).st_mode
    except OSError:
        os.close(fd)
        raise
    if stat.S_ISREG(mode):
        return True
    os.close(fd)
    return False


def _flock_shared_bounded(fd: int) -> bool:
    """Take a shared flock on ``fd``, waiting about ``_LOCK_TIMEOUT`` s.

    An in-place rewrite (_rewrite_in_place) holds an exclusive flock on the
    file while it writes, so a read under this lock sees the old content or
    the new content, never a mix of both. A replaced file needs no lock: the
    reader keeps the inode it opened.

    True when the lock was taken. False where flock doesn't exist or doesn't
    work (Windows, some network file systems): as for the rewrite, the read
    then goes ahead unlocked. Raises _ConfigBusy when the lock stayed taken
    the whole time: the file isn't read, so nothing from it is used.
    """
    try:
        import fcntl
    except ImportError:
        return False
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
        if time.monotonic() >= deadline:
            raise _ConfigBusy(errno.EWOULDBLOCK, _E_READ_BUSY)
        time.sleep(_LOCK_STEP / 5)


def _read_capped(f: Any) -> Any:
    """Read at most the byte cap (plus one) from an open config file, under
    a shared lock on that same file (see _flock_shared_bounded)."""
    fd = f.fileno()
    locked = _flock_shared_bounded(fd)
    try:
        data = f.read(_MAX_CONFIG_BYTES + 1)
    finally:
        if locked:
            import fcntl

            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ConfigTooLarge("config too large")
    return data


def _yaml_from_text(text: str, name: str = "config.yaml") -> Any:
    """Parse YAML text as if read from the file ``name``.

    Every config parse goes through here. Parsing from a plain string makes
    PyYAML quote the offending lines in its error, and a config line can
    hold an API key; a named stream gives the file name and position
    instead, never the content. The input is capped in bytes, the loader
    bounds merge keys, nesting and numbers while it loads (see
    _ConfigLoader), and the result is capped in size and depth.
    """
    return _yaml_load(_ConfigLoader(_named_stream(text, name)))


def _named_stream(text: str, name: str) -> io.StringIO:
    if len(text) > _MAX_CONFIG_BYTES:
        raise ConfigTooLarge("config too large")
    stream = io.StringIO(text)
    stream.name = name
    return stream


def _yaml_load(loader: _ConfigLoader) -> Any:
    try:
        data = loader.get_single_data()
    except RecursionError:  # a backstop: the composer limit comes first
        raise ConfigTooLarge("config nested too deeply") from None
    finally:
        loader.dispose()
    _check_config_size(data)
    return data


class _KeyWatchingLoader(_ConfigLoader):
    """_ConfigLoader that notes a watched key repeated in one mapping."""

    def __init__(self, stream: Any, watch: frozenset[str]) -> None:
        super().__init__(stream)
        self._watch = watch
        self.repeated_key = False

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = key_node.value
                if key in self._watch and key in seen:
                    self.repeated_key = True
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_config_text(
    text: str, repeated: frozenset[str] = frozenset()
) -> tuple[Any, bool]:
    """Parse config text with every limit a config load applies.

    For a reader that needs only its own settings from text that Config or
    read_user_config_text read, such as telemetry's opt-out. Also returns
    whether a key in ``repeated`` appears twice in one mapping. Raises
    ConfigTooLarge (a ValueError) or yaml.YAMLError, as a load does.
    """
    loader = _KeyWatchingLoader(_named_stream(text, "config.yaml"), repeated)
    return _yaml_load(loader), loader.repeated_key


def read_user_config_text(path: Path | str) -> str | None:
    """The text of a user config file, read once the way Config reads one.

    The open is non-blocking and for a regular file only, the read is
    capped at 64 KiB and waits for an in-place rewrite to finish, and the
    text is parsed with every load limit. For a reader that needs only its
    own settings from a file Config didn't load, such as telemetry's
    opt-out from the default file when --config names another one.

    Raises FileNotFoundError when there is no file. None when a load would
    refuse the file: not a regular file, too large, a parse error or a
    bomb, or a rewrite that didn't finish within the wait. In an AI agent
    session, also None for a file a load wouldn't trust: the default file
    must be the real-home config and pass the same checks.
    """
    from .output import is_ai_agent

    path = Path(path)
    if is_ai_agent():
        location = _trust_root_location()
        if location is not None and os.path.abspath(path) == os.path.abspath(
            location[1]
        ):
            root = _open_trust_root_text()
            if root is not None:
                return root[2]
            if not os.path.lexists(path):
                raise FileNotFoundError(errno.ENOENT, "no config file", str(path))
            return None
        if _same_file(path, _default_config_file()):
            # The default file away from the real home: never trusted.
            if not os.path.lexists(path):
                raise FileNotFoundError(errno.ENOENT, "no config file", str(path))
            return None
    try:
        with _open_config(path) as reader:
            reader.load()
            return reader.text
    except FileNotFoundError:
        raise
    except (ValueError, yaml.YAMLError, OSError, RecursionError):
        return None  # incl. a FIFO, device or directory (_NotRegularFile)


def _load_error_summary(exc: BaseException) -> str:
    """Why a config file couldn't be loaded, without any of its content."""
    if isinstance(exc, yaml.YAMLError):
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            return f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}"
        return "invalid YAML"
    if isinstance(exc, ConfigTooLarge):
        return "it's too large or too deeply nested"
    if isinstance(exc, UnicodeDecodeError):
        return "not UTF-8 text"
    if isinstance(exc, OSError):
        text = exc.strerror or type(exc).__name__
        return text[:1].lower() + text[1:]
    if isinstance(exc, (ValueError, TypeError, AttributeError, KeyError, IndexError)):
        return "a setting has an invalid value or type"
    return f"an unexpected error ({type(exc).__name__})"


def _read_for_update(path: Path) -> tuple[os.stat_result | None, Any]:
    """The file's fstat and the YAML in ``path`` as it is now, for a save to
    apply its changes to, both from one open.

    ``(None, {})`` when there is no file, ``{}`` when the file is empty. A path that leads to
    a device, FIFO or socket raises _NotRegularFile (the open never waits on
    a FIFO), so a save never replaces one. Read and parse errors are raised.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _O_BINARY)
    except FileNotFoundError:
        return None, {}
    st = os.fstat(fd)
    if not _is_regular_fd(fd, st):
        raise _NotRegularFile(errno.EINVAL, "not a regular file", str(path))
    with os.fdopen(fd, "rb") as f:
        data = _yaml_from_text(_read_capped(f).decode("utf-8"))
    return st, {} if data is None else data


def _save_refusal(st: os.stat_result | None) -> str | None:
    """Why an AI agent session may not save over this file (a _WHY_* key).

    A file another user owns, or one others can write, keeps its mode and
    content: rewriting it at 0600 would turn their content into trusted
    content. None when the save may go ahead.
    """
    if st is None or sys.platform == "win32":
        return None
    from .output import is_ai_agent

    if not is_ai_agent():
        return None
    if st.st_uid != os.geteuid():
        return _WHY_OWNER
    if stat.S_IMODE(st.st_mode) & 0o022:
        return _WHY_WRITABLE
    return None


class ConfigSaveRefused(OSError):
    """An explicit save refused in an AI agent session; ``hint`` says who
    fixes it."""

    hint = msg.W_CFG_UNTRUSTED_FILE_HINT


def _save_refused(why: str, config_path: Path) -> ConfigSaveRefused:
    return ConfigSaveRefused(
        errno.EPERM,
        msg.W_CFG_SAVE_UNTRUSTED.format(why=msg.W_CFG_UNTRUSTED_WHY[why]),
        str(config_path),
    )


@contextmanager
def _directory_lock(directory: Path) -> Iterator[None]:
    """Hold an exclusive flock on ``directory`` (POSIX; a no-op elsewhere).

    The directory, not a lock file: it needs no write access and leaves
    nothing behind, and it covers both the rename and the in-place path.
    Raises _ConfigBusy when another process holds it for about 5 s.
    """
    try:
        fd = os.open(directory, os.O_RDONLY | _O_DIRECTORY)
    except OSError:
        # e.g. Windows, where a directory can't be opened: no lock.
        yield
        return
    try:
        if not _flock_bounded(fd):
            raise _ConfigBusy(errno.EWOULDBLOCK, "config lock busy", str(directory))
        yield
    finally:
        os.close(fd)


def _rewrite_in_place(target: Path, text: str) -> None:
    """Rewrite a file that can't be replaced (e.g. a bind mount) at 0600.

    Refuses a file with other hard links, and a key that would stay readable
    by others because the mode can't be changed. The file is never
    truncated before the new content is on disk: at every point it holds
    the old YAML or the new YAML, followed only by blank lines, so a killed
    process or a full disk can't leave half a config.
    """
    new = text.encode("utf-8")
    fd = os.open(target, os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | _O_BINARY)
    try:
        # Serialize writers of this file from the size check to the last
        # fsync (POSIX; skipped where flock doesn't exist or work).
        if not _flock_bounded(fd):
            raise _ConfigBusy(errno.EWOULDBLOCK, "config lock busy", str(target))
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _NotRegularFile(errno.EINVAL, _E_NOT_REGULAR, str(target))
        if st.st_nlink > 1:
            raise OSError(errno.EMLINK, f"{target} has other hard links; not written")
        mode = stat.S_IMODE(st.st_mode)
        if hasattr(os, "fchmod") and mode & 0o077:
            # A writer that doesn't own the file can't change its mode.
            with suppress(OSError):
                os.fchmod(fd, 0o600)
                mode = 0o600
            if mode & 0o077 and "api_key" in text:
                raise PermissionError(
                    errno.EPERM,
                    f"{target} holds an API key and can't be restricted to 0600",
                )
        old_size = st.st_size
        if len(new) > old_size:
            # Reserve the space first: blank lines after the old YAML.
            try:
                _write_all(fd, b"\n" * (len(new) - old_size), old_size)
                os.fsync(fd)
            except OSError:
                with suppress(OSError):
                    os.ftruncate(fd, old_size)
                raise
        # The new YAML, padded with blank lines over any old tail.
        _write_all(fd, new + b"\n" * max(0, old_size - len(new)), 0)
        os.fsync(fd)
        os.ftruncate(fd, len(new))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes, offset: int) -> None:
    os.lseek(fd, offset, os.SEEK_SET)
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _flatten(data: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """Flatten nested dicts to ``{path: leaf}``; empty dicts are leaves."""
    if isinstance(data, dict) and data:
        out: dict[tuple[str, ...], Any] = {}
        for key, value in data.items():
            out.update(_flatten(value, (*prefix, str(key))))
        return out
    return {prefix: data}


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``path`` in nested dicts; ``None`` deletes the key."""
    current = data
    for part in path[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            if value is None:
                return
            nxt = {}
            current[part] = nxt
        current = nxt
    if value is None:
        current.pop(path[-1], None)
    else:
        current[path[-1]] = copy.deepcopy(value)


def _model_default(path: tuple[str, ...]) -> Any:
    """The built-in default for a config leaf (``profiles.<name>.*`` included)."""
    if len(path) == 3 and path[0] == "profiles":
        return ProfileConfig().model_dump().get(path[2])
    return _flatten(DeepgramConfig().model_dump()).get(path)


def _host_entries(hosts: Any) -> list[str]:
    """Allowlist entries as host[:port]; a scheme or path written by habit
    (``https://h.example/``) is stripped, and a single string is one entry."""
    if isinstance(hosts, str):
        hosts = [hosts]
    if not isinstance(hosts, list):
        return []
    entries: list[str] = []
    for entry in hosts:
        if not isinstance(entry, str) or not entry.strip():
            continue
        entry = entry.strip()
        if "://" in entry:
            origin = parse_origin(entry)
            if origin is None:
                continue
            entry = origin.origin.split("://", 1)[1]
        entries.append(entry.split("/", 1)[0].lower())
    return entries


def _holds_api_key(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("api_key"):
        return True
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        return any(
            isinstance(p, dict) and bool(p.get("api_key")) for p in profiles.values()
        )
    return False


# Profile credential fields an untrusted user file can't set: the key, and
# its expiry (a past or invalid expiry deletes the keyring credential).
_CREDENTIAL_FIELDS = ("api_key", "dg_token_expires_at")


def _credential_paths(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Dotted paths of the credential ``fields`` set in a user config."""
    paths = [f for f in fields if data.get(f)]
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if isinstance(profile, dict):
                paths += [f"profiles.{name}.{f}" for f in fields if profile.get(f)]
    return paths


def _without_api_keys(data: dict[str, Any]) -> dict[str, Any]:
    """A copy of a user config with every API key and its expiry removed."""
    data = copy.deepcopy(data)
    for field in _CREDENTIAL_FIELDS:
        data.pop(field, None)
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict):
                for field in _CREDENTIAL_FIELDS:
                    profile.pop(field, None)
    return data


class Config:
    """Cross-platform configuration manager."""

    def __init__(
        self,
        config_path: str | None = None,
        profile: str | None = None,
        base_url: str | None = None,
    ):
        """Initialize configuration manager.

        Args:
            config_path: Optional path to configuration file
            profile: Optional profile name to use
            base_url: Optional base URL from the command line (``--base-url``).
                It applies to this run only and is never saved.
        """
        self.config_path = (
            Path(config_path) if config_path else self._get_default_config_path()
        )
        self._explicit_profile = profile  # Store explicitly set profile
        self._flag_base_url = base_url
        # Only the config file at the default location may widen trust
        # (api.allowed_hosts, a non-regional profile base_url, tool paths).
        # A --config file elsewhere could come from a cloned repository.
        # Decided when the file is read (see _read_user_file).
        self._trusted_file = False
        # Set when the default file is distrusted in an AI agent session.
        self._untrusted_reason: str | None = None
        self._config: DeepgramConfig
        # Raw user-file data: the only layer save() writes back.
        self._user_data: dict[str, Any] = {}
        # True when the user file exists but couldn't be read or parsed:
        # save() then refuses to overwrite it.
        self._load_failed = False
        # The user config path leads to a FIFO, device or directory.
        self._load_not_regular = False
        # Another dg process held the user config's lock for the whole wait:
        # nothing from the file is used, and telemetry is off for this run.
        self._load_busy = False
        # Effective values right after loading; save() writes what changed.
        self._baseline: dict[str, Any] = {}
        # Paths a command set explicitly (written even if equal to baseline).
        self._explicit: set[tuple[str, ...]] = set()
        # Where each profile's base_url came from (see hosts.SOURCE_*).
        self._base_url_sources: dict[str, str] = {}
        self._base_url_override: tuple[str, str] | None = None
        self._override_profile: str | None = None
        # base_url values confirmed by a person (dg login --save-base-url).
        self._approved_base_urls: dict[str, str] = {}
        self._loading = False
        # Text of the user config file as read by _load_config; None when the
        # file was absent or couldn't be read.
        self.user_config_text: str | None = None
        self._load_config()

    @property
    def profile(self) -> str | None:
        """Get the current profile name.

        Precedence: explicit profile > active profile > default profile
        """
        return (
            self._explicit_profile
            or self._config.active_profile
            or self._config.default_profile
        )

    @profile.setter
    def profile(self, value: str | None) -> None:
        """Set the profile name."""
        self._explicit_profile = value

    @property
    def user_config_refused(self) -> bool:
        """True when the user config file exists but wasn't used as read:
        it couldn't be read or parsed (too large, a bomb, not a regular file,
        a parse error, a rewrite that didn't finish within the wait), or it
        isn't trusted in this AI agent session. A reader of its own settings
        from ``user_config_text``, such as telemetry's opt-out, then treats
        the file as opted out and never reads it again."""
        return (
            self._load_failed
            or self._untrusted_reason is not None
            or self._key_untrusted
        )

    def _get_default_config_path(self) -> Path:
        """Get the default configuration path for the current platform."""
        # Use platformdirs for cross-platform config directory
        config_dir = Path(platformdirs.user_config_dir("deepctl", "deepgram"))
        _private_mkdir(config_dir)

        # Migrate from old location if needed
        self._migrate_config_if_needed(config_dir)

        return config_dir / "config.yaml"

    def _migrate_config_if_needed(self, new_config_dir: Path) -> None:
        """Migrate config from old deepgram directory to new deepctl
        directory."""
        # Check for old config location
        old_config_dir = Path(platformdirs.user_config_dir("deepgram", "deepgram"))
        old_config_path = old_config_dir / "config.yaml"
        new_config_path = new_config_dir / "config.yaml"

        from .output import is_ai_agent

        # Never in an AI agent session: the copy would become the trusted
        # config from a file the agent may control.
        if is_ai_agent():
            return
        # If old config exists and new doesn't, migrate it
        if old_config_path.exists() and not new_config_path.exists():
            try:
                print(f"Migrating config from {old_config_path} to {new_config_path}")
                text = old_config_path.read_text(encoding="utf-8")
                # Written like any save: 0600, atomic, never via the source's mode.
                with _directory_lock(new_config_dir):
                    self._replace_or_rewrite(new_config_path, text)
                print("✓ Config migrated successfully")
            except Exception as e:
                print(f"Warning: Could not migrate config: {e}")

    def _get_project_config_path(self) -> Path:
        """Get the project-specific configuration path."""
        return Path.cwd() / "deepgram.yaml"

    def _load_config(self) -> None:
        """Load configuration from all sources."""
        self._loading = True
        try:
            self._load_layers()
        finally:
            self._loading = False
        if self._load_busy:
            # The saved settings are unknown, the telemetry opt-out included.
            self._config.telemetry.enabled = False
            self._trusted_file = False
        # A single host written as a string is a one-item list.
        hosts = self._config.api.allowed_hosts
        if isinstance(hosts, str):
            self._config.api.allowed_hosts = [hosts]
        self._baseline = self._config.model_dump(warnings=False)

    def _load_layers(self) -> None:
        # Start with default configuration
        self._config = DeepgramConfig()
        self._key_untrusted = False
        self._key_untrusted_why: str | None = None

        # Load from user config file. The raw text is kept so other readers
        # (telemetry's opt-out) can parse the same bytes without opening the
        # file again; a FIFO or /dev/stdin can only be read once.
        self.user_config_text = None
        if self.config_path.exists():
            try:
                user_config = self._read_user_file()
                if user_config:
                    if isinstance(user_config, dict):
                        self._user_data = copy.deepcopy(user_config)
                        if (
                            self._check_permissions(user_config)
                            and not self._trusted_file
                        ):
                            # Tightened just now: decide trust on the new mode.
                            self._untrusted_reason = None
                            user_config = self._read_user_file()
                            if isinstance(user_config, dict):
                                self._user_data = copy.deepcopy(user_config)
                        if self._key_untrusted:
                            self._warn_untrusted_credentials(user_config)
                            user_config = _without_api_keys(user_config)
                        if not self._trusted_file:
                            user_config = self._filter_custom_config(user_config)
                    self._merge_config(user_config)
                    if isinstance(user_config, dict):
                        self._note_base_url_sources(user_config, SOURCE_USER)
            except Exception as e:
                # Don't fail on config load errors, just warn
                self._load_failed = True
                self._load_not_regular = isinstance(e, _NotRegularFile)
                self._load_busy = isinstance(e, _ConfigBusy)
                _queue_notice(
                    "W-CFG-LOAD",
                    str(self.config_path),
                    f"Could not load config from "
                    f"{msg.display(str(self.config_path))}: {_load_error_summary(e)}",
                    None,
                    quiet_ok=False,
                )

        # Load from project config file, through the allowlist
        project_config_path = self._get_project_config_path()
        if _exists(project_config_path):
            try:
                with _open_config(project_config_path) as f:
                    project_config = f.load()
                    if project_config:
                        allowed = self._filter_project_config(project_config)
                        self._merge_config(allowed)
                        self._note_base_url_sources(allowed, SOURCE_PROJECT)
            except Exception as e:
                _queue_notice(
                    "W-CFG-LOAD",
                    str(project_config_path),
                    f"Could not load project config from "
                    f"{msg.display(str(project_config_path))}: "
                    f"{_load_error_summary(e)}",
                    None,
                    quiet_ok=False,
                )

        # Override with environment variables
        self._load_env_config()

        # The --base-url flag wins over every other source.
        if self._flag_base_url:
            self._set_config_value("base_url", self._flag_base_url)
            self._base_url_sources[self.profile or "default"] = SOURCE_FLAG
            self._base_url_override = (self._flag_base_url, SOURCE_FLAG)
            self._override_profile = self.profile or "default"

    def _read_user_file(self) -> Any:
        """Read the user config and decide whether it may widen trust.

        Outside an AI agent session the file at the default location is
        trusted. In one, only the real-home default is, and only when it
        passes the checks in _open_trust_root; its content is used only if
        the configured file is that same file. Sets ``user_config_text``
        from the same read that supplies the returned data.
        """
        from .output import is_ai_agent

        is_default = _same_file(self.config_path, _default_config_file())
        if not is_ai_agent():
            self._trusted_file = is_default
            with _open_config(self.config_path) as f:
                data = f.load()
                self.user_config_text = f.text
                return data
        location = _trust_root_location()
        if location is not None and os.path.abspath(
            self.config_path
        ) == os.path.abspath(location[1]):
            # The configured file is the trust root's path: one checked open
            # decides trust and supplies the values.
            for attempt in range(5):
                root_st, root_data, why, root_text = _check_trust_root()
                # A dg save replacing the file just after it was opened
                # leaves the open copy with no links (and some file systems
                # briefly report none under load): look again, every time a
                # full check.
                replaced = (
                    why == _WHY_LINKS
                    and root_st is not None
                    and (root_st.st_nlink == 0)
                )
                if (not replaced and why != _WHY_UNREADABLE) or attempt == 4:
                    break
                time.sleep(0.01 * (attempt + 1))
            if root_st is not None and why is None:
                self._trusted_file = True
                self.user_config_text = root_text
                return root_data
            if why == _WHY_NOT_REGULAR:
                # Reported like any unreadable config (W-CFG-LOAD).
                raise _NotRegularFile(
                    errno.EINVAL, "not a regular file", str(self.config_path)
                )
            self._trusted_file = False
            st, data, text = _read_yaml_text_fd(self.config_path)
            if st is not None and not stat.S_ISREG(st.st_mode):
                # e.g. a link to a device: not read, and reported.
                raise _NotRegularFile(
                    errno.EINVAL, "not a regular file", str(self.config_path)
                )
            self.user_config_text = text
            self._untrusted_reason = _untrusted_reason(why)
            return data
        for _attempt in range(3):
            st, data, text = _read_yaml_text_fd(self.config_path)
            self.user_config_text = text
            if st is None:
                break
            if not stat.S_ISREG(st.st_mode):
                # A FIFO, device or directory: reported like any unreadable
                # config (W-CFG-LOAD), as outside an agent session.
                raise _NotRegularFile(
                    errno.EINVAL, "not a regular file", str(self.config_path)
                )
            root = _open_trust_root_text()
            if root is None:
                break
            if _same_inode(st, root[0]):
                self._trusted_file = True
                self.user_config_text = root[2]
                return root[1]
            # Another dg process may have replaced the file between the two
            # opens: try again; a mismatch never trusts.
        self._trusted_file = False
        if is_default:
            # Ignored keys are reported one by one (see _warn_custom_key).
            self._untrusted_reason = msg.W_CFG_UNTRUSTED
        return data

    def _note_base_url_sources(self, data: dict[str, Any], source: str) -> None:
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            return
        for name, profile in profiles.items():
            if isinstance(profile, dict) and profile.get("base_url"):
                self._base_url_sources[str(name)] = source

    @property
    def api_key_untrusted(self) -> bool:
        """True when this AI agent session didn't use an API key the user
        file holds (a keyless untrusted file gives the usual missing-key
        error)."""
        return self._key_untrusted and _holds_api_key(self._user_data)

    def _warn_untrusted_credentials(self, data: dict[str, Any]) -> None:
        """One W-CFG-UNTRUSTED line, naming the cause, per credential field
        that isn't used. A writable file's API keys are covered by
        W-PERM-AGENT instead."""
        why = self._key_untrusted_why or _WHY_WRITABLE
        reason = msg.W_CFG_UNTRUSTED_FILE.format(why=msg.W_CFG_UNTRUSTED_WHY[why])
        fields = _CREDENTIAL_FIELDS if why == _WHY_OWNER else ("dg_token_expires_at",)
        for key in _credential_paths(data, fields):
            _queue_notice(
                "W-CFG-UNTRUSTED",
                f"{self.config_path}:{key}",
                msg.W_CF.format(
                    key=msg.display(key),
                    config_file=msg.display(os.path.abspath(self.config_path)),
                    reason=reason,
                ),
                msg.W_CFG_UNTRUSTED_FILE_HINT,
                quiet_ok=False,
            )

    def _check_permissions(self, user_config: dict[str, Any]) -> bool:
        """Restrict a user config that holds an API key to 0600 (POSIX).

        In an AI agent session, a file another user owns or others can change
        is never tightened, and its credentials (keys and their expiry) aren't
        used, whether or not it holds a key. Returns True when the mode was
        changed.
        """
        if sys.platform == "win32":
            return False
        try:
            st = os.stat(self.config_path)
        except OSError:
            return False
        mode = stat.S_IMODE(st.st_mode)
        from .output import is_ai_agent

        if is_ai_agent() and (st.st_uid != os.geteuid() or mode & 0o022):
            self._key_untrusted = True
            self._key_untrusted_why = (
                _WHY_OWNER if st.st_uid != os.geteuid() else _WHY_WRITABLE
            )
        if not _holds_api_key(user_config):
            return False
        if self._key_untrusted_why == _WHY_OWNER:
            # Its owner controls the content. The W-CFG-UNTRUSTED "owner"
            # lines say it all: no chmod, and no W-PERM-* line.
            return False
        if not mode & 0o077:
            return False
        shown = msg.display_path(self.config_path)
        if mode & 0o022 and is_ai_agent():
            # Others could have written it: in an AI agent session it stays
            # as it is and stays untrusted (W-CFG-UNTRUSTED "writable"), its
            # API key included. Making it 0600 here would turn their content
            # into trusted content. Read-only exposure (0644) is tightened.
            self._key_untrusted = True
            _queue_notice(
                "W-PERM-AGENT",
                str(self.config_path),
                msg.W_PERM_AGENT.format(user_config=shown, octal=f"{mode:04o}"),
                msg.W_CFG_UNTRUSTED_FILE_HINT,
                quiet_ok=False,
            )
            return False
        try:
            os.chmod(self.config_path, 0o600)
        except OSError as exc:
            # A file dg can't change (another owner, a read-only store such
            # as home-manager's): chmod advice wouldn't work.
            foreign = st.st_uid != os.getuid() or exc.errno == errno.EROFS
            text = msg.W_PERM_FAILED_FOREIGN if foreign else msg.W_PERM_FAILED
            _queue_notice(
                "W-PERM-FAILED",
                str(self.config_path),
                text.format(user_config=shown, octal=f"{mode:04o}"),
                None,
                quiet_ok=False,
            )
            return False
        _queue_notice(
            "I-PERM-FIXED",
            str(self.config_path),
            msg.I_PERM_FIXED.format(user_config=shown),
            None,
            quiet_ok=True,
        )
        return True

    def _filter_project_config(self, data: Any) -> dict[str, Any]:
        """Return only the project-file settings a repository may choose.

        Output settings and a regional ``base_url`` pass. Everything else is
        dropped with one warning per key that names the key, never its value.
        """
        if not isinstance(data, dict):
            return {}
        allowed: dict[str, Any] = {}
        for key, value in data.items():
            key = str(key)
            if key == "output" and isinstance(value, dict):
                fields = OutputConfig.model_fields
                kept = {k: v for k, v in value.items() if k in fields}
                for k in value:
                    if k not in fields:
                        self._warn_project_key("W-PF-OTHER", f"output.{k}")
                if kept:
                    allowed["output"] = kept
            elif key == "profiles" and isinstance(value, dict):
                profiles = self._filter_project_profiles(value)
                if profiles:
                    allowed["profiles"] = profiles
            elif key == "base_url":
                if not is_regional_base_url(value):
                    self._warn_project_base_url(key, value)
            elif key in _PROJECT_FORBIDDEN_TOP_KEYS:
                self._warn_project_key(_PROJECT_FORBIDDEN_TOP_KEYS[key], key)
            elif key in _PROJECT_FORBIDDEN_SECTIONS:
                message_id = _PROJECT_FORBIDDEN_SECTIONS[key]
                leaves = _first_leaves(value, (key,), 4)
                if len(leaves) > 3:
                    leaves = [f"{key}.*"]
                for leaf in leaves:
                    self._warn_project_key(message_id, leaf)
            else:
                self._warn_project_key("W-PF-OTHER", key)
        return allowed

    def _filter_custom_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Read a ``--config`` file outside the default location.

        Credentials, profiles and output settings pass. Settings that widen
        trust or run programs (``api.*``, ``tools.*``, ``plugins.*``,
        ``update.*``) are dropped, and a profile ``base_url`` must be a
        regional Deepgram URL, exactly as for a project file.
        """
        kept: dict[str, Any] = {}
        for key, value in data.items():
            key = str(key)
            if key in ("api", "tools", "plugins", "update"):
                message_id = f"W-CF-{key.upper()}"
                leaves = _first_leaves(value, (key,), 4)
                if len(leaves) > 3:
                    leaves = [f"{key}.*"]
                for leaf in leaves:
                    self._warn_custom_key(message_id, leaf)
            elif key == "profiles" and isinstance(value, dict):
                profiles: dict[str, Any] = {}
                for name, profile in value.items():
                    if not isinstance(profile, dict):
                        continue
                    profile = dict(profile)
                    url = profile.get("base_url")
                    if url is not None and not is_regional_base_url(url):
                        del profile["base_url"]
                        origin = parse_origin(url) if isinstance(url, str) else None
                        self._warn_custom_key(
                            "W-CF-BASEURL",
                            f"profiles.{name}.base_url",
                            origin=msg.display(origin.origin if origin else "this URL"),
                        )
                    elif url is not None:
                        profile["base_url"] = canonical_url(url)
                    profiles[str(name)] = profile
                kept["profiles"] = profiles
            else:
                kept[key] = value
        return kept

    def _filter_project_profiles(self, profiles: dict[Any, Any]) -> dict[str, Any]:
        kept: dict[str, Any] = {}
        for name, profile in profiles.items():
            name = str(name)
            if not isinstance(profile, dict):
                self._warn_project_key("W-PF-OTHER", f"profiles.{name}")
                continue
            for key, value in profile.items():
                path = f"profiles.{name}.{key}"
                if key == "base_url":
                    if is_regional_base_url(value):
                        kept.setdefault(name, {})["base_url"] = canonical_url(value)
                    else:
                        self._warn_project_base_url(path, value)
                elif key in _PROJECT_FORBIDDEN_PROFILE_KEYS:
                    self._warn_project_key(_PROJECT_FORBIDDEN_PROFILE_KEYS[key], path)
                else:
                    self._warn_project_key("W-PF-OTHER", path)
        return kept

    def _warn_project_key(
        self,
        message_id: str,
        key: str,
        origin: str = "",
        source: str = msg.PROJECT_FILE_DISPLAY,
    ) -> None:
        from .output import is_agentic, is_ai_agent

        reason, hint, agent_hint = msg.W_PF_MESSAGES[message_id]
        # Hints point at the one config file that can approve these settings.
        user_config = msg.display_path(_default_config_file())
        text = msg.W_PF.format(
            key=msg.display(key),
            project_file=source,
            reason=reason.format(origin=origin),
        )
        if agent_hint is not None and (is_agentic() or is_ai_agent()):
            hint = agent_hint
        _queue_notice(
            message_id,
            f"{source}:{key}",
            text,
            hint.format(user_config=user_config),
            quiet_ok=False,
        )

    def _warn_custom_key(self, message_id: str, key: str, origin: str = "") -> None:
        from .output import is_agentic, is_ai_agent

        if self._untrusted_reason is not None:
            # The default file, distrusted in this AI agent session.
            _queue_notice(
                "W-CFG-UNTRUSTED",
                f"{self.config_path}:{key}",
                msg.W_CF.format(
                    key=msg.display(key),
                    config_file=msg.display(os.path.abspath(self.config_path)),
                    reason=self._untrusted_reason,
                ),
                # The file itself refused: the setting already is in the
                # user config, so the hint is about the file.
                msg.AGENT_SETTINGS_HINT
                if self._untrusted_reason == msg.W_CFG_UNTRUSTED
                else msg.W_CFG_UNTRUSTED_FILE_HINT,
                quiet_ok=False,
            )
            return
        reason, hint, agent_hint = msg.W_CF_MESSAGES[message_id]
        text = msg.W_CF.format(
            key=msg.display(key),
            config_file=msg.display_path(self.config_path),
            reason=reason.format(origin=origin),
        )
        if agent_hint is not None and (is_agentic() or is_ai_agent()):
            hint = agent_hint
        _queue_notice(
            message_id,
            f"{self.config_path}:{key}",
            text,
            hint.format(user_config=msg.display_path(_default_config_file())),
            quiet_ok=False,
        )

    def _warn_project_base_url(
        self, key: str, value: Any, source: str = msg.PROJECT_FILE_DISPLAY
    ) -> None:
        origin = parse_origin(value) if isinstance(value, str) else None
        shown = msg.display(origin.origin if origin else "this URL")
        self._warn_project_key("W-PF-BASEURL", key, origin=shown, source=source)

    def _merge_config(self, config_dict: dict[str, Any]) -> None:
        """Merge configuration dictionary into current config."""
        # Deep merge configuration
        if "profiles" in config_dict:
            for profile_name, profile_config in config_dict["profiles"].items():
                if profile_name not in self._config.profiles:
                    self._config.profiles[profile_name] = ProfileConfig()

                # Update profile config
                for key, value in profile_config.items():
                    if hasattr(self._config.profiles[profile_name], key):
                        setattr(self._config.profiles[profile_name], key, value)

        # Update other top-level config
        for key, value in config_dict.items():
            if key != "profiles" and hasattr(self._config, key):
                if isinstance(getattr(self._config, key), BaseModel):
                    # Handle nested models
                    nested_model = getattr(self._config, key)
                    for nested_key, nested_value in value.items():
                        if hasattr(nested_model, nested_key):
                            setattr(nested_model, nested_key, nested_value)
                else:
                    setattr(self._config, key, value)

    def _load_env_config(self) -> None:
        """Load configuration from environment variables."""
        # Handle case-insensitive environment variables on Windows
        env_vars = {}
        for key, value in os.environ.items():
            env_vars[key.upper()] = value

        # Map environment variables to config
        env_mappings = {
            "DEEPGRAM_API_KEY": ("api_key", str),
            "DEEPGRAM_PROJECT_ID": ("project_id", str),
            "DEEPGRAM_BASE_URL": ("base_url", str),
            "DEEPGRAM_OUTPUT_FORMAT": ("output.format", str),
            "DEEPGRAM_OUTPUT_COLOR": ("output.color", bool),
            "DEEPGRAM_PROFILE": ("default_profile", str),
        }

        for env_key, (config_path, config_type) in env_mappings.items():
            if env_key in env_vars:
                value = env_vars[env_key]
                converted_value: Any = value
                if config_type == bool:
                    converted_value = value.lower() in (
                        "true",
                        "1",
                        "yes",
                        "on",
                    )

                self._set_config_value(config_path, converted_value)
                if config_path == "base_url":
                    self._base_url_sources[self.profile or "default"] = SOURCE_ENV
                    self._base_url_override = (value, SOURCE_ENV)
                    self._override_profile = self.profile or "default"

    def _set_config_value(self, path: str, value: Any) -> None:
        """Set a configuration value using dot notation.

        Outside of loading, this is an explicit change that save() writes,
        except ``base_url`` (see :meth:`approve_base_url`).
        """
        if not self._loading:
            if path in ("api_key", "project_id", "base_url"):
                profile_name = self.profile or self._config.default_profile
                self._explicit.add(("profiles", profile_name, path))
            else:
                self._explicit.add(tuple(path.split(".")))
        if "." in path:
            # Handle nested paths like "output.format"
            parts = path.split(".")
            current = self._config

            for part in parts[:-1]:
                current = getattr(current, part)

            setattr(current, parts[-1], value)
        else:
            # Handle top-level paths
            if path == "api_key" or path == "project_id" or path == "base_url":
                # These belong to the current profile
                profile_name = self.profile or self._config.default_profile
                if profile_name not in self._config.profiles:
                    self._config.profiles[profile_name] = ProfileConfig()
                setattr(self._config.profiles[profile_name], path, value)
            else:
                setattr(self._config, path, value)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation."""
        try:
            if "." in key:
                parts = key.split(".")
                current = self._config

                for part in parts:
                    if hasattr(current, part):
                        current = getattr(current, part)
                    else:
                        return default

                return current
            else:
                return getattr(self._config, key, default)
        except (AttributeError, KeyError):
            return default

    def get_profile(self, profile_name: str | None = None) -> ProfileConfig:
        """Get configuration for a specific profile."""
        profile_name = profile_name or self.profile or self._config.default_profile

        if profile_name not in self._config.profiles:
            self._config.profiles[profile_name] = ProfileConfig()

        return self._config.profiles[profile_name]

    def base_url_source(self, profile_name: str | None = None) -> str:
        """Where the profile's effective ``base_url`` came from."""
        name = profile_name or self.profile or self._config.default_profile
        return self._base_url_sources.get(name, SOURCE_DEFAULT)

    @property
    def base_url_override(self) -> tuple[str, str] | None:
        """``(url, source)`` from ``--base-url`` or ``DEEPGRAM_BASE_URL``."""
        return self._base_url_override

    def unsaved_base_url(
        self, profile_name: str | None = None
    ) -> tuple[str, str] | None:
        """``(used, saved)`` when this run's ``--base-url`` or
        ``DEEPGRAM_BASE_URL`` differs from the base URL the user config file
        holds for the profile (which it will keep), else ``None``.
        """
        name = profile_name or self.profile or self._config.default_profile
        if self._base_url_override is None or name in self._approved_base_urls:
            return None
        if name != self._override_profile:
            return None  # this profile didn't use the override
        profiles = self._user_data.get("profiles")
        saved_profile = profiles.get(name) if isinstance(profiles, dict) else None
        saved = (
            saved_profile.get("base_url") if isinstance(saved_profile, dict) else None
        )
        if not isinstance(saved, str) or not saved:
            saved = DEFAULT_BASE_URL
        used = self._base_url_override[0]
        return None if used == saved else (used, saved)

    @property
    def trusted_file(self) -> bool:
        """True when the config file is the one at the default location."""
        return self._trusted_file

    def allowed_hosts(self) -> list[str]:
        """``api.allowed_hosts`` from the default user config file only.

        With a ``--config`` file elsewhere, the list is read from the default
        file (when its location is trusted), never from the ``--config`` file.
        """
        if self._trusted_file:
            hosts: Any = self._config.api.allowed_hosts
        else:
            hosts = self._default_file_allowed_hosts()
        return _host_entries(hosts)

    def _default_file_allowed_hosts(self) -> Any:
        """The allowlist from the trusted default file, read once."""
        from .output import is_ai_agent

        try:
            if is_ai_agent():
                location = _trust_root_location()
                if location is None or _same_file(self.config_path, location[1]):
                    return []  # none, or the configured file itself (untrusted)
                root = _open_trust_root()
                data = root[1] if root is not None else None
            else:
                default = _default_config_file()
                if _same_file(self.config_path, default):
                    return []
                _, data = _read_yaml_fd(default)
        except (OSError, yaml.YAMLError, ValueError):
            return []
        api = data.get("api") if isinstance(data, dict) else None
        return api.get("allowed_hosts") if isinstance(api, dict) else []

    def approve_base_url(self, profile_name: str, url: str) -> None:
        """Use and save ``url`` as the profile's base URL.

        Only for a value a person confirmed in a terminal (``dg login
        --save-base-url``). It is written on the next save().
        """
        self.get_profile(profile_name).base_url = url
        self._approved_base_urls[profile_name] = url
        self._base_url_sources[profile_name] = SOURCE_USER

    def add_allowed_host(self, entry: str) -> None:
        """Append ``entry`` to ``api.allowed_hosts`` (written on save())."""
        hosts = self.allowed_hosts()
        if entry not in hosts:
            self._config.api.allowed_hosts = [*hosts, entry]
            self._explicit.add(("api", "allowed_hosts"))

    def save(self, *, implicit: bool = False) -> None:
        """Save configuration to the user config file.

        A user file that exists but couldn't be read or parsed is never
        overwritten: an explicit save raises OSError, and an ``implicit``
        one (a cache or bookkeeping write) is skipped. The same holds when
        another process keeps the config locked for about 5 s.

        Writes the values a command changed or set explicitly onto the user
        file as it is at write time, read again under the lock, so values
        another dg process saved in the meantime are kept. Project-file and
        environment values, unknown keys and sections this version doesn't
        model are left as they are. A profile
        ``base_url`` is written only through :meth:`approve_base_url`, so a
        URL from ``--base-url`` or ``DEEPGRAM_BASE_URL`` is never persisted.
        """
        if self._load_failed:
            if implicit:
                return
            if self._load_busy:
                raise OSError(errno.EWOULDBLOCK, _E_SAVE_BUSY, str(self.config_path))
            raise OSError(
                errno.EINVAL,
                _E_NOT_REGULAR
                if self._load_not_regular
                else "couldn't read this config file, so nothing was saved. Fix the "
                "error shown above, then run the command again",
                str(self.config_path),
            )
        current = self._config.model_dump(warnings=False)
        before = _flatten(self._baseline)
        after = _flatten(current)

        def is_base_url(path: tuple[str, ...]) -> bool:
            return len(path) == 3 and path[0] == "profiles" and path[2] == "base_url"

        changes: list[tuple[tuple[str, ...], Any]] = []
        for path, old in before.items():
            if not path or path in after or is_base_url(path):
                continue
            if old == {} and any(p[: len(path)] == path for p in after):
                continue  # an empty section that gained keys: nothing removed
            changes.append((path, None))
        for path, value in after.items():
            if not path or is_base_url(path):
                continue
            explicit = any(path[: len(e)] == e for e in self._explicit)
            if path not in before and not explicit and value == _model_default(path):
                # e.g. a profile get_profile() created just to read it
                continue
            if explicit or before.get(path, object()) != value:
                changes.append((path, value))

        # Deleted profiles go entirely; approved base URLs are written.
        deleted = [
            name
            for name in self._baseline.get("profiles", {})
            if name not in self._config.profiles
        ]
        approved = {
            name: url
            for name, url in self._approved_base_urls.items()
            if name in self._config.profiles
        }

        def apply(data: dict[str, Any]) -> None:
            for path, value in changes:
                _set_path(data, path, value)
            profiles = data.get("profiles")
            if isinstance(profiles, dict):
                for name in deleted:
                    profiles.pop(name, None)
            for name, url in approved.items():
                _set_path(data, ("profiles", name, "base_url"), url)

        data = self._write_user_file(apply, implicit=implicit)
        if data is None:
            return
        self._user_data = data
        self._baseline = current
        self._explicit.clear()

    def check_saveable(self) -> None:
        """Raise the error an explicit save would raise for the config file
        as it is now, without writing anything.

        For callers that store a secret elsewhere (the keyring) before
        saving, so a save that can't happen (a link to /dev/null) stops them
        first.
        """
        try:
            st = os.stat(os.path.realpath(self.config_path))
        except OSError:
            return  # missing, or an error the save itself reports
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, _E_NOT_REGULAR, str(self.config_path))
        why = _save_refusal(st)
        if why is not None:
            raise _save_refused(why, self.config_path)
        if not self._load_failed and not _loads_back(
            yaml.safe_dump(self._user_data, default_flow_style=False, sort_keys=False)
        ):
            # The file loaded, but written back it wouldn't: no save of it can
            # succeed (the same check a save makes).
            raise OSError(errno.EFBIG, _E_WOULD_NOT_LOAD, str(self.config_path))

    def _write_user_file(
        self,
        apply: Callable[[dict[str, Any]], None],
        *,
        implicit: bool = False,
    ) -> dict[str, Any] | None:
        """Apply ``apply`` to the user config file and write it at 0600.

        Under a lock on the target's directory the file is read again,
        ``apply`` changes that data, and the result is written. A symlinked
        config is written through to its target. The new content goes to a
        0600 temp file next to the target and replaces it atomically; where
        the target can't be replaced (a single-file bind mount, a directory
        dg can't write), it is rewritten in place and restricted to 0600.

        Returns the data written, or None for an ``implicit`` save that was
        skipped because the file couldn't be read or stayed locked. Errors
        name the config file.
        """
        try:
            return self._write_user_file_locked(apply, implicit)
        except _ConfigBusy:
            if implicit:
                return None
            raise OSError(
                errno.EWOULDBLOCK, _E_SAVE_BUSY, str(self.config_path)
            ) from None
        except OSError as exc:
            if exc.errno is None or exc.filename == str(self.config_path):
                raise
            raise OSError(exc.errno, exc.strerror, str(self.config_path)) from exc

    def _write_user_file_locked(
        self, apply: Callable[[dict[str, Any]], None], implicit: bool
    ) -> dict[str, Any] | None:
        target = Path(os.path.realpath(self.config_path))
        _private_mkdir(target.parent)
        if _same_file(self.config_path, _default_config_file()):
            # The default config directory itself, not a symlink's target dir.
            _tighten_dir(self.config_path.parent)
        with _directory_lock(target.parent):
            st: os.stat_result | None = None
            try:
                st, data = _read_for_update(target)
            except _ConfigBusy:
                raise
            except (OSError, ValueError, yaml.YAMLError) as exc:
                data = exc
            if not isinstance(data, dict):
                if implicit:
                    return None
                raise OSError(
                    errno.EINVAL,
                    _E_NOT_REGULAR
                    if isinstance(data, _NotRegularFile)
                    else "couldn't read this config file, so nothing was saved. "
                    "Fix the error shown above, then run the command again",
                    str(self.config_path),
                ) from None  # the YAML error's text can hold file content
            why = _save_refusal(st)
            if why is not None:
                if implicit:
                    return None
                raise _save_refused(why, self.config_path)
            apply(data)
            text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
            if not _loads_back(text):
                # dg never writes a file it would refuse to load.
                if implicit:
                    return None
                raise OSError(errno.EFBIG, _E_WOULD_NOT_LOAD, str(self.config_path))
            self._replace_or_rewrite(target, text)
        return data

    def _replace_or_rewrite(self, target: Path, text: str) -> None:
        import secrets

        tmp = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        except OSError as exc:
            # A directory we can't create files in (a single-file bind mount
            # owned by root, a read-only directory): rewrite the file itself.
            if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS) and os.access(
                target, os.W_OK
            ):
                _rewrite_in_place(target, text)
                return
            raise
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                _replace(tmp, target)
            except OSError as exc:
                if exc.errno not in (errno.EBUSY, errno.EXDEV):
                    raise
                _rewrite_in_place(target, text)
            _fsync_dir(target.parent)
        finally:
            with suppress(OSError):
                os.unlink(tmp)

    def list_profiles(self) -> list[str]:
        """List all available profiles."""
        return list(self._config.profiles.keys())

    def create_profile(self, name: str, **kwargs: Any) -> None:
        """Create or replace a profile and save it.

        The profile keeps its current effective ``base_url`` for this run,
        but a ``base_url`` passed here is not saved unless it was approved
        (see :meth:`approve_base_url`).
        """
        kwargs.pop("base_url", None)
        existing = self._config.profiles.get(name)
        base_url = existing.base_url if existing is not None else DEFAULT_BASE_URL
        self._config.profiles[name] = ProfileConfig(base_url=base_url, **kwargs)
        for key in kwargs:
            self._explicit.add(("profiles", name, key))
        self.save()

    def update_profile(self, name: str, **fields: Any) -> None:
        """Change only the given fields of a profile and save them.

        Other fields keep their values, and values that came from the
        environment stay out of the file.
        """
        fields.pop("base_url", None)
        profile = self.get_profile(name)
        for key, value in fields.items():
            setattr(profile, key, value)
            self._explicit.add(("profiles", name, key))
        self.save()

    def delete_profile(self, name: str) -> None:
        """Delete a profile."""
        if name in self._config.profiles:
            del self._config.profiles[name]
            self.save()

    @property
    def config_dir(self) -> Path:
        """Get the configuration directory."""
        return self.config_path.parent
