"""Disposable caches for ``dg --help``: plugin discovery and help text.

**Plugin discovery.** Root ``--help`` lists plugin commands, so it has to
discover them, which costs an entry point scan and an import of every
plugin. The result is kept in ``<cache>/discovery/plugins-<crc>.json`` and
reused while its stamp holds. The stamp covers every directory on
``sys.path`` and the plugin venv (``~/.deepctl/plugins``, under ``$HOME``
and under the account's real home): installing or removing a package
changes one of their mtimes. Only a clean result is kept: a discovery
that wrote anything to stderr (a skipped plugin, a load error) or let a
plugin replace a built-in runs again next time, so its warnings show on
every ``--help`` as in 0.4.x. A row stores its summary as the paragraphs
Click shows, so a multi-line help caches too. Entries are checked again
when read: a file holding an invalid or reserved name, or text with a
character ``unsafe_text`` refuses (Unicode categories Cc, Cf except ZWNJ
and ZWJ, Zl, Zp, Co and Cn), is ignored (a fresh discovery runs), and
built-ins always win. Under an AI agent this cache lives under the
real home's cache directory, never one ``$HOME`` or ``XDG_CACHE_HOME``
points to.

**Help text** (for the native launcher; contract in
``docs/contracts/help-cache.md``). Only when the launcher asks
(``DEEPCTL_HELP_CACHE=1``, removed from the environment at startup), for
argv exactly ``--help``, outside an AI agent, and for a clean run (exit 0,
nothing on stderr), Python saves stdout to
``<real cache>/help/<version>-<prog>-<width>-<tty|pipe>.txt`` with
``<entry>.key.json`` next to it, listing every input the text depends on.

Directories are 0700 and files 0600, written atomically. A cache directory
that isn't a real directory owned by this user, or that its group or others
can write (the 0.4.1 O1 rule), is never read or written. Any error here
skips the cache; it never fails the run.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from typing import Any

FORMAT = 1

# The project file, read from the working directory.
PROJECT_FILE = "deepgram.yaml"

# Environment variables root --help output, or the plugin set, depends on.
HELP_ENV_VARS = (
    "COLUMNS",
    "TERM",
    "NO_COLOR",
    "FORCE_COLOR",
    "DEEPGRAM_OUTPUT_COLOR",
    "DEEPCTL_TELEMETRY_DISABLED",
    "DO_NOT_TRACK",
    "DEEPCTL_TELEMETRY",
    "CI",
    "DEEPCTL_EAGER_LOAD",
    "XDG_CONFIG_HOME",
    "HOME",
    "OR_APP_NAME",
    "OR_SITE_URL",
)


# Plugin command names (the 0.4.2 rule; plugin_manager._VALID_PLUGIN_NAME).
VALID_PLUGIN_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}")
# Format characters a cached row may carry: ZWNJ and ZWJ, which Persian,
# Indic scripts and emoji sequences need. Every other Cf character (bidi
# controls, zero-width spaces, the BOM, soft hyphens, invisible tags) is
# refused, as are Cc (other than tab, and newline in help text), Zl, Zp, Co
# and Cn.
_ALLOWED_FORMAT = frozenset("\u200c\u200d")
_REFUSED_CATEGORIES = frozenset({"Zl", "Zp", "Co", "Cn"})


def unsafe_text(text: str, allow_newline: bool) -> bool:
    """Whether ``text`` holds a character a cached row may not carry."""
    import unicodedata

    for char in text:
        category = unicodedata.category(char)
        if category == "Cc":
            if char == "\t" or (allow_newline and char == "\n"):
                continue
            return True
        if category == "Cf":
            if char not in _ALLOWED_FORMAT:
                return True
        elif category in _REFUSED_CATEGORIES:
            return True
    return False


# Limits on one help-cache key, so the launcher's stat budget stays fixed.
# write_help writes no key over either limit; the launcher treats one as a
# miss. Entry text over MAX_ENTRY_BYTES isn't written or served either.
MAX_KEY_FILES = 32
MAX_KEY_ENV = 64
MAX_ENTRY_BYTES = 256 * 1024


def list_widths() -> range:
    """The widths Click can wrap a command summary to in root ``--help``.

    ``HelpFormatter`` is ``max(min(columns, 80) - 2, 50)`` wide (50-78; dg
    sets no ``terminal_width``), and ``write_dl`` wraps the second column to
    ``max(width - first_col - 2, 10)``, where ``first_col`` is the longest
    listed name, capped at 30, plus 2. The built-ins are always listed, so
    ``first_col`` is at least the longest visible built-in name plus 2.
    """
    from deepctl import _manifest

    shortest_first_col = 2 + min(
        30,
        max(
            len(name)
            for name, entry in _manifest.top_level().items()
            if not entry.get("hidden")
        ),
    )
    narrowest = max(50 - 32 - 2, 10)
    widest = max(78 - shortest_first_col - 2, 10)
    return range(narrowest, widest + 1)


def cache_root() -> str | None:
    """``user_cache_dir("deepctl", "deepgram")``, or ``None`` if unknown."""
    try:
        import platformdirs

        return str(platformdirs.user_cache_dir("deepctl", "deepgram"))
    except Exception:
        return None


def real_cache_root() -> str | None:
    """The default cache directory derived from the account itself, with
    ``HOME`` and ``XDG_CACHE_HOME`` ignored: ``<pw_dir>/.cache/deepctl`` on
    Linux, ``<pw_dir>/Library/Caches/deepctl`` on macOS and
    ``<LocalAppData>\\deepgram\\deepctl\\Cache`` on Windows. ``None`` when
    the OS can't say."""
    if sys.platform == "win32":
        base = _windows_local_appdata()
        return (
            None if base is None else os.path.join(base, "deepgram", "deepctl", "Cache")
        )
    home = _real_home()
    if home is None:
        return None
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches", "deepctl")
    return os.path.join(home, ".cache", "deepctl")


def _windows_local_appdata() -> str | None:
    """The LocalAppData known folder from the OS, not the environment."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        if shell32.SHGetFolderPathW(None, 0x1C, None, 0, buf) == 0:
            return buf.value or None
    except Exception:
        return None
    return None


def _safe_dir(path: str) -> bool:
    """A real directory (no symlink) owned by this user that its group and
    others can't write (0.4.1 rule O1)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if sys.platform == "win32":
        return True
    return info.st_uid == os.getuid() and not info.st_mode & 0o022


def _private_dir(name: str, create: bool, real: bool = False) -> str | None:
    """``<cache>/<name>``, checked (and created 0700 when ``create``).
    ``real`` uses the real home's cache directory (``real_cache_root``)."""
    root = real_cache_root() if real else cache_root()
    if root is None:
        return None
    path = os.path.join(root, name)
    if create:
        try:
            os.makedirs(root, mode=0o700, exist_ok=True)
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            return None
    if _safe_dir(root) and _safe_dir(path):
        return path
    return None


def _write_private(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, mode 0600, through a fresh
    temp file in the same directory (removed again on any failure)."""
    import tempfile

    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune_stale_tmp(directory: str, max_age: float = 3600.0) -> None:
    """Remove temp files a crashed writer left behind (older than an hour)."""
    now = time.time()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not name.endswith(".tmp"):
            continue
        path = os.path.join(directory, name)
        try:
            if now - os.lstat(path).st_mtime > max_age:
                os.unlink(path)
        except OSError:
            pass


def _read_private(path: str) -> bytes | None:
    """``path``'s bytes when it's a regular file owned by this user that its
    group and others can't write; else ``None``."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            return None
        if sys.platform != "win32" and (
            info.st_uid != os.getuid() or info.st_mode & 0o022
        ):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = -1
            return f.read()
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def file_state(path: str) -> dict[str, Any]:
    """What the help-cache key records about one file or directory.

    ``mode`` and ``uid`` are there because the trust checks read them and
    chmod or chown changes neither mtime nor size. A path ``stat`` can't
    follow but ``lstat`` finds (a dangling symlink) is recorded from
    ``lstat``, so a reader's ``stat`` fails on it and never matches;
    ``absent`` means not even ``lstat`` finds it."""
    try:
        info = os.stat(path)
    except OSError:
        try:
            info = os.lstat(path)
        except OSError:
            return {"path": path, "absent": True}
    return {
        "path": path,
        "mtime_ns": info.st_mtime_ns,
        "size": info.st_size,
        "inode": info.st_ino,
        "mode": info.st_mode,
        "uid": info.st_uid,
    }


def _real_home() -> str | None:
    """The account's home from the OS (``HOME`` ignored); None on Windows."""
    if sys.platform == "win32":
        return None
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_dir or None
    except (ImportError, KeyError, OSError):
        return None


def plugin_state_paths() -> list[str]:
    """The files and directories whose change can change the plugin set."""
    paths = [os.path.abspath(p or os.curdir) for p in sys.path]
    homes = {os.path.expanduser("~")}
    real = _real_home()
    if real:
        homes.add(real)
    for home in sorted(homes):
        plugins = os.path.join(home, ".deepctl", "plugins")
        venv = os.path.join(plugins, "venv")
        paths += [plugins, os.path.join(plugins, "plugins.json"), venv]
        for lib in ("lib", "Lib"):
            lib_dir = os.path.join(venv, lib)
            paths.append(lib_dir)
            try:
                entries = sorted(os.listdir(lib_dir))
            except OSError:
                continue
            for entry in entries:
                if entry == "site-packages" or entry.startswith("python"):
                    site = (
                        os.path.join(lib_dir, entry)
                        if entry == "site-packages"
                        else os.path.join(lib_dir, entry, "site-packages")
                    )
                    paths.append(site)
    return list(dict.fromkeys(paths))


def _plugin_dirs() -> list[str]:
    """dg's own plugin directories (``.deepctl``, ``.deepctl/plugins``,
    the venv) under each home: the ones the plugin loader's trust check
    reads the owner and mode of."""
    homes = {os.path.expanduser("~")}
    real = _real_home()
    if real:
        homes.add(real)
    paths: list[str] = []
    for home in sorted(homes):
        dot = os.path.join(home, ".deepctl")
        plugins = os.path.join(dot, "plugins")
        paths += [dot, plugins, os.path.join(plugins, "venv")]
    return paths


def _plugin_dir_trust() -> list[Any]:
    """Owner and mode of ``_plugin_dirs``. chmod and chown change neither
    mtime nor size, so the stamp records them itself."""
    trust: list[Any] = []
    for path in _plugin_dirs():
        try:
            info = os.stat(path)
        except OSError:
            trust.append([path, None])
            continue
        trust.append([path, info.st_mode, info.st_uid])
    return trust


def _agent_context() -> list[str]:
    """Agent mode decides which plugin venv is used (and the working
    directory can rule it out), so both are part of the stamp."""
    from deepctl_core.output import is_ai_agent

    return ["agent", os.getcwd()] if is_ai_agent() else ["person"]


def plugin_stamp() -> str:
    """Everything ``plugin_state_paths`` and the run context say about the
    plugin set, as one string (compared whole: no hashing on this path)."""
    from deepctl import __version__

    state = {
        "format": FORMAT,
        "version": __version__,
        "python": list(sys.version_info[:3]),
        "executable": sys.executable,
        "context": _agent_context(),
        "files": [file_state(p) for p in plugin_state_paths()],
        "trust": _plugin_dir_trust(),
    }
    return json.dumps(state, sort_keys=True)


def _discovery_file(directory: str) -> str:
    import zlib

    prefix = zlib.crc32(sys.prefix.encode("utf-8", "replace"))
    return os.path.join(directory, f"plugins-{prefix:08x}.json")


def _discovery_dir(create: bool) -> str | None:
    """Under an AI agent, only the real home's cache directory is used."""
    from deepctl_core.output import is_ai_agent

    return _private_dir("discovery", create=create, real=is_ai_agent())


def _clean_entry(entry: Any, reserved: frozenset[str]) -> dict[str, Any] | None:
    """A cached plugin command as it may be listed, or None to drop it."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or VALID_PLUGIN_NAME.fullmatch(name) is None:
        return None
    if name in reserved:
        return None
    texts: dict[str, Any] = {}
    for key, allow_newline in (("summary", False), ("help", True)):
        value = entry.get(key)
        if value is not None and (
            not isinstance(value, str) or unsafe_text(value, allow_newline)
        ):
            return None
        texts[key] = value
    more = entry.get("summary_more", [])
    if not isinstance(more, list) or not all(
        isinstance(p, str) and p and not unsafe_text(p, False) for p in more
    ):
        return None
    if more:
        if texts["summary"] is None:
            return None
        texts["summary_more"] = list(more)
    deprecated = entry.get("deprecated")
    if not (isinstance(deprecated, str) and deprecated):
        deprecated = deprecated is True
    return {
        "name": name,
        **texts,
        "hidden": entry.get("hidden") is True,
        "deprecated": deprecated,
    }


def _listed_text(short_help: str | None, help_text: str | None, deprecated: Any) -> str:
    """The text Click puts in the command list for these attributes, before
    it wraps it (``Command.get_short_help_str``)."""
    import click

    return click.Command(
        "x", short_help=short_help, help=help_text, deprecated=deprecated
    ).get_short_help_str()


def listing_row(name: str, command: Any) -> dict[str, Any] | None:
    """The discovery-cache row for a live command, or None when the cache
    can't reproduce its row in ``--help`` exactly.

    Click shows ``short_help`` whole, wrapped, with paragraphs kept and
    line breaks (``\\n``, ``\\r\\n``, ``\\f``) inside a paragraph joined by a
    space. The row stores those paragraphs (``summary`` and
    ``summary_more``), so no stored summary holds a line break, and the row
    is kept only if rebuilding ``short_help`` from them renders the same
    text at every width the command list can use (anything else, such as a
    ``\\b`` block, isn't cached). ``help`` is stored only when there is no
    ``short_help``, since only then does the list read it.
    """
    import inspect

    from click.formatting import wrap_text

    short_help = command.short_help
    deprecated = command.deprecated or False
    row: dict[str, Any] = {
        "name": name,
        "summary": None,
        "help": None,
        "hidden": bool(command.hidden),
        "deprecated": deprecated,
    }
    if not short_help:
        row["help"] = command.help
        return row
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in [*inspect.cleandoc(short_help).expandtabs().splitlines(), ""]:
        if not line:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
        else:
            buf.append(line if buf else line.lstrip())
    if not paragraphs:
        return None
    rebuilt = "\n\n".join(paragraphs)
    original = _listed_text(short_help, None, deprecated)
    candidate = _listed_text(rebuilt, None, deprecated)
    if candidate != original:
        for width in list_widths():
            if wrap_text(original, width, preserve_paragraphs=True) != wrap_text(
                candidate, width, preserve_paragraphs=True
            ):
                return None
    row["summary"] = paragraphs[0]
    if paragraphs[1:]:
        row["summary_more"] = paragraphs[1:]
    return row


def read_discovery(
    stamp: str, reserved: frozenset[str] = frozenset()
) -> list[dict[str, Any]] | None:
    """The plugin commands of a clean discovery with this stamp, or None.

    Entries are checked again: a file holding an invalid name, a
    ``reserved`` name (the built-in commands and aliases) or unsafe text is
    ignored (None: discover again), since the writer never stores one;
    ``hidden``/``deprecated`` are coerced.
    """
    directory = _discovery_dir(create=False)
    if directory is None:
        return None
    data = _read_private(_discovery_file(directory))
    if data is None:
        return None
    try:
        cached = json.loads(data)
    except ValueError:
        return None
    if not isinstance(cached, dict) or cached.get("stamp") != stamp:
        return None
    commands = cached.get("commands")
    if not isinstance(commands, list):
        return None
    cleaned = [_clean_entry(entry, reserved) for entry in commands]
    if any(entry is None for entry in cleaned):
        # The writer never stores such a row: the file was changed by
        # something else. Discover again rather than trust any of it.
        return None
    return [entry for entry in cleaned if entry is not None]


def write_discovery(
    stamp: str,
    commands: list[dict[str, Any]],
    reserved: frozenset[str] = frozenset(),
) -> None:
    """Keep a clean discovery's plugin commands for this stamp.

    A discovery with any row ``read_discovery`` would drop (the same check,
    the same ``reserved`` names) isn't kept: it runs again each time, so the
    listing is the same on every run.
    """
    if any(_clean_entry(entry, reserved) is None for entry in commands):
        return
    try:
        directory = _discovery_dir(create=True)
        if directory is None:
            return
        _prune_stale_tmp(directory)
        data = json.dumps({"stamp": stamp, "commands": commands}, indent=1)
        _write_private(_discovery_file(directory), data.encode("utf-8"))
    except OSError:
        pass


def _subcommands_file(directory: str) -> str:
    import zlib

    prefix = zlib.crc32(sys.prefix.encode("utf-8", "replace"))
    return os.path.join(directory, f"subcommands-{prefix:08x}.json")


def _builtin_only_groups(directory: str, stamp: str) -> list[str]:
    data = _read_private(_subcommands_file(directory))
    if data is None:
        return []
    try:
        cached = json.loads(data)
    except ValueError:
        return []
    if not isinstance(cached, dict) or cached.get("stamp") != stamp:
        return []
    groups = cached.get("builtin_only")
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        return []
    return groups


def read_builtin_only(stamp: str, group: str) -> bool:
    """True when a scan with this stamp found only the built-in
    subcommands' own entry points for ``group``. Next to the discovery
    cache, with the same directory, ownership and agent rules; anything
    else in the file means "scan again"."""
    directory = _discovery_dir(create=False)
    if directory is None:
        return False
    return group in _builtin_only_groups(directory, stamp)


def write_builtin_only(stamp: str, group: str) -> None:
    """Record that ``group``'s entry points are all built-in for ``stamp``
    (only that outcome is kept: any other scans again each run)."""
    try:
        directory = _discovery_dir(create=True)
        if directory is None:
            return
        groups = _builtin_only_groups(directory, stamp)
        if group in groups:
            return
        _prune_stale_tmp(directory)
        data = json.dumps({"stamp": stamp, "builtin_only": sorted([*groups, group])})
        _write_private(_subcommands_file(directory), data.encode("utf-8"))
    except OSError:
        pass


def help_width() -> int:
    """The width Click formats help for: the terminal's, at most 80."""
    import shutil

    return min(shutil.get_terminal_size().columns, 80)


def key_inputs() -> dict[str, Any]:
    """The interpreter, files and environment variables root ``--help``
    output depends on, as the key records them. ``run()`` takes this before
    rendering, and ``write_help`` writes nothing unless it still holds.

    Besides the plugin set and the user config, the key records the
    project file in the working directory (``cwd_relative``, so a reader
    looks in its own) and every input of the agent detection table
    (``deepctl_core.agents.hard_inputs``): a key written by a person is
    never served to an agent session, nor under other trust state."""
    env_names, marker_files = _agent_markers()
    paths = [*plugin_state_paths(), *_plugin_dirs(), _default_config_file()]
    files = [file_state(p) for p in dict.fromkeys([*paths, *marker_files])]
    files.append({**file_state(PROJECT_FILE), "cwd_relative": True})
    return {
        "executable": sys.executable,
        "files": files,
        "env": {
            name: os.environ.get(name, None)
            for name in sorted(set(HELP_ENV_VARS) | set(env_names))
        },
    }


def help_key(
    prog: str,
    width: int,
    tty: bool,
    text: bytes,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``<entry>.key.json``: every input root ``--help`` output depends on,
    plus ``text_sha256``, the hash of the entry's bytes, which binds the key
    to the text it was written with."""
    import hashlib

    from deepctl import __version__

    inputs = key_inputs() if inputs is None else inputs
    return {
        "format": FORMAT,
        "version": __version__,
        "prog": prog,
        "width": width,
        "isatty": tty,
        **inputs,
        "text_sha256": hashlib.sha256(text).hexdigest(),
    }


def _agent_markers() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The variables (``DG_AGENT`` too) and files agent detection reads."""
    from deepctl_core.agents import OVERRIDE_VAR, hard_inputs

    env_names, files = hard_inputs()
    return (*env_names, OVERRIDE_VAR), files


def _default_config_file() -> str:
    import platformdirs

    return os.path.join(
        platformdirs.user_config_dir("deepctl", "deepgram"), "config.yaml"
    )


def help_entry_name(version: str, prog: str, width: int, tty: bool) -> str:
    safe_prog = "".join(
        c if (c.isascii() and c.isalnum()) or c in "-_." else "_" for c in prog
    )
    return f"{version}-{safe_prog}-{width}-{'tty' if tty else 'pipe'}.txt"


def help_dir(create: bool) -> str | None:
    """``<real cache>/help``: never under ``HOME`` or ``XDG_CACHE_HOME``."""
    return _private_dir("help", create=create, real=True)


def write_help(
    text: str,
    prog: str,
    width: int,
    tty: bool,
    before: dict[str, Any] | None = None,
) -> None:
    """Save root ``--help`` output and its key; prune other versions.

    Nothing is written when ``before`` (``key_inputs()`` taken before the
    text was rendered) no longer matches, when the key would list more than
    ``MAX_KEY_FILES`` files or ``MAX_KEY_ENV`` variables, when the text is
    over ``MAX_ENTRY_BYTES``, when the key would hold text that isn't valid
    Unicode (a path or variable with undecodable bytes), or when the entry
    on disk already has the same key and text, so both files keep their
    mtime.
    """
    from deepctl import __version__

    try:
        directory = help_dir(create=True)
        if directory is None:
            return
        inputs = key_inputs()
        if before is not None and inputs != before:
            return
        if len(inputs["files"]) > MAX_KEY_FILES or len(inputs["env"]) > MAX_KEY_ENV:
            return  # a key no launcher would use
        name = help_entry_name(__version__, prog, width, tty)
        body = text.encode("utf-8")
        if len(body) > MAX_ENTRY_BYTES:
            return
        # ensure_ascii=False and strict UTF-8: a surrogate-escaped path or
        # value raises here, and no entry is written.
        key = json.dumps(
            help_key(prog, width, tty, body, inputs), indent=1, ensure_ascii=False
        ).encode("utf-8")
        entry = os.path.join(directory, name)
        if _read_private(f"{entry}.key.json") == key and _read_private(entry) == body:
            return
        _prune_stale_tmp(directory)
        # Text first, key second: a reader checks text_sha256, so text from
        # another run under this key is a miss, never served.
        _write_private(entry, body)
        _write_private(f"{entry}.key.json", key)
        for other in os.listdir(directory):
            if other.endswith(".tmp"):
                continue
            if not other.startswith(f"{__version__}-"):
                try:
                    os.unlink(os.path.join(directory, other))
                except OSError:
                    pass
    except (OSError, UnicodeError):
        pass


def served_text(entry: str) -> bytes | None:
    """The entry's text if its key binds it, else None.

    This is the reference for the contract's ``format`` and ``text_sha256``
    rules only. It doesn't check the header fields, ``executable``, ``files``
    or ``env`` (rules 1-4), so it isn't a full evaluator."""
    import hashlib

    body = _read_private(entry)
    key_bytes = _read_private(f"{entry}.key.json")
    if body is None or key_bytes is None:
        return None
    try:
        key = json.loads(key_bytes)
    except ValueError:
        return None
    if not isinstance(key, dict) or key.get("format") != FORMAT:
        return None
    if key.get("text_sha256") != hashlib.sha256(body).hexdigest():
        return None
    return body


class StderrWatch:
    """Wraps ``sys.stderr`` and notes whether anything was written to it."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.written = False

    def write(self, text: Any) -> Any:
        if text:
            self.written = True
        return self.stream.write(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)
