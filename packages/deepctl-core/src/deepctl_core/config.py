"""Cross-platform configuration management for deepctl.

Three layers are read: the user config file, the project ``deepgram.yaml``
in the current directory, and ``DEEPGRAM_*`` environment variables. Only the
user file is ever written, and only with the values a command explicitly
changed, atomically and readable by the owner only.
"""

from __future__ import annotations

import atexit
import copy
import errno
import hashlib
import io
import os
import re
import secrets
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


class ProfileConfig(BaseModel):
    """Configuration for a specific profile."""

    api_key: str | None = None
    project_id: str | None = None
    base_url: str = "https://api.deepgram.com"
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

    while True:
        with _notices_lock:
            if not _pending_notices:
                return
            message_id, text, hint, quiet_ok = _pending_notices.pop(0)
        try:
            if message_id.startswith("I-"):
                if quiet_ok and output._output_config["quiet"]:
                    continue
                output.print_info(escape(text), stderr=True)
            else:
                output.print_security_warning(escape(text))
            if hint:
                output.print_hint(escape(hint))
        except (OSError, ValueError):
            pass


def _default_config_file() -> Path:
    """The user config file at its default location (no side effects)."""
    return Path(platformdirs.user_config_dir("deepctl", "deepgram")) / "config.yaml"


def default_config_file() -> Path:
    """The user config file at its default location: the only file that can
    approve API hosts, so every hint names it."""
    return _default_config_file()


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
    bomb, or a rewrite that didn't finish within the wait.
    """
    path = Path(path)
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


def _read_for_update(path: Path) -> Any:
    """The YAML in ``path`` as it is now, for a save to apply its changes to.

    ``{}`` when there is no file or the file is empty. A path that leads to
    a device, FIFO or socket raises _NotRegularFile (the open never waits on
    a FIFO), so a save never replaces one. Read and parse errors are raised.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _O_BINARY)
    except FileNotFoundError:
        return {}
    if not _is_regular_fd(fd):
        raise _NotRegularFile(errno.EINVAL, "not a regular file", str(path))
    with os.fdopen(fd, "rb") as f:
        data = _yaml_from_text(_read_capped(f).decode("utf-8"))
    return {} if data is None else data


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


class Config:
    """Cross-platform configuration manager."""

    def __init__(self, config_path: str | None = None, profile: str | None = None):
        """Initialize configuration manager.

        Args:
            config_path: Optional path to configuration file
            profile: Optional profile name to use
        """
        self.config_path = (
            Path(config_path) if config_path else self._get_default_config_path()
        )
        self._explicit_profile = profile  # Store explicitly set profile
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
        a parse error, a rewrite that didn't finish within the wait). A
        reader of its own settings from ``user_config_text``, such as
        telemetry's opt-out, then treats the file as opted out and never
        reads it again."""
        return self._load_failed

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

        # If old config exists and new doesn't, migrate it
        if old_config_path.exists() and not new_config_path.exists():
            try:
                import shutil

                print(f"Migrating config from {old_config_path} to {new_config_path}")
                shutil.copy2(old_config_path, new_config_path)
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
        self._baseline = self._config.model_dump(warnings=False)

    def _load_layers(self) -> None:
        # Start with default configuration
        self._config = DeepgramConfig()

        # Load from user config file. The raw text is kept so other readers
        # (telemetry's opt-out) can parse the same bytes without opening the
        # file again; a FIFO or /dev/stdin can only be read once.
        self.user_config_text = None
        if self.config_path.exists():
            try:
                with _open_config(self.config_path) as f:
                    user_config = f.load()
                    self.user_config_text = f.text
                    if user_config:
                        if isinstance(user_config, dict):
                            self._user_data = copy.deepcopy(user_config)
                            self._check_permissions(user_config)
                        self._merge_config(user_config)
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

        # Load from project config file
        project_config_path = self._get_project_config_path()
        if _exists(project_config_path):
            try:
                with _open_config(project_config_path) as f:
                    project_config = f.load()
                    if project_config:
                        self._merge_config(project_config)
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

    def _check_permissions(self, user_config: dict[str, Any]) -> bool:
        """Restrict a user config that holds an API key to 0600 (POSIX).

        Returns True when the mode was changed.
        """
        if sys.platform == "win32" or not _holds_api_key(user_config):
            return False
        try:
            st = os.stat(self.config_path)
        except OSError:
            return False
        mode = stat.S_IMODE(st.st_mode)
        if not mode & 0o077:
            return False
        shown = msg.display_path(self.config_path)
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

    def _set_config_value(self, path: str, value: Any) -> None:
        """Set a configuration value using dot notation.

        Outside of loading, this is an explicit change that save() writes.
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
        model are left as they are.
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

        changes: list[tuple[tuple[str, ...], Any]] = []
        for path, old in before.items():
            if not path or path in after:
                continue
            if old == {} and any(p[: len(path)] == path for p in after):
                continue  # an empty section that gained keys: nothing removed
            changes.append((path, None))
        for path, value in after.items():
            if not path:
                continue
            explicit = any(path[: len(e)] == e for e in self._explicit)
            if path not in before and not explicit and value == _model_default(path):
                # e.g. a profile get_profile() created just to read it
                continue
            if explicit or before.get(path, object()) != value:
                changes.append((path, value))

        # Deleted profiles go entirely.
        deleted = [
            name
            for name in self._baseline.get("profiles", {})
            if name not in self._config.profiles
        ]

        def apply(data: dict[str, Any]) -> None:
            for path, value in changes:
                _set_path(data, path, value)
            profiles = data.get("profiles")
            if isinstance(profiles, dict):
                for name in deleted:
                    profiles.pop(name, None)

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
            try:
                data = _read_for_update(target)
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
        """Create or replace a profile and save it."""
        self._config.profiles[name] = ProfileConfig(**kwargs)
        for key in kwargs:
            self._explicit.add(("profiles", name, key))
        self.save()

    def update_profile(self, name: str, **fields: Any) -> None:
        """Change only the given fields of a profile and save them.

        Other fields keep their values, and values that came from the
        environment stay out of the file.
        """
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
