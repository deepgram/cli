# Help cache contract (Python ↔ native launcher)

Python writes the help cache, and the native launcher reads it to print `dg --help` without starting Python. This page is the whole contract: build the reader from it alone. The key format is `docs/contracts/help-cache-key.schema.json`. The writer is `deepctl/_cache.py`, and `served_text()` there is the reference for the last check.

## Where

The cache always lives under the default cache directory derived from the account, never from `HOME` or `XDG_CACHE_HOME`:

| OS | Directory |
|---|---|
| Linux and other Unix | `<pw_dir>/.cache/deepctl/help/`, where `pw_dir` comes from `getpwuid(getuid())` |
| macOS | `<pw_dir>/Library/Caches/deepctl/help/` |
| Windows | `<LocalAppData>\deepgram\deepctl\Cache\help\`, where `<LocalAppData>` is `SHGetFolderPathW(CSIDL_LOCAL_APPDATA = 0x1C)` |

If the account has no home (no passwd entry, or the call fails), there is no cache.

Both `<cache>` (the parent) and `help/` must pass these checks, or the cache is neither read nor written:
- a real directory (`lstat`), not a symlink;
- on Unix, owned by the current uid, with no group or other write bit.

Python creates both directories with mode 0700.

## Entry names

An entry is named `<version>-<prog>-<width>-<tty>.txt`.

- **`<version>`:** the deepctl version, for example `0.5.0`.
- **`<prog>`:** the program name in the usage line, with every character outside ASCII `[A-Za-z0-9._-]` replaced by `_`. Python derives the name in this order:
  1. `DEEPCTL_PROG_NAME`, with surrounding whitespace removed, when that leaves it non-empty.
  2. Otherwise `dg` under `python -m deepctl`.
  3. Otherwise Click's name: the basename of `argv[0]` (on Windows, pip's launcher has already dropped `.exe`).

  The launcher should set `DEEPCTL_PROG_NAME` to the basename of its own `argv[0]`, without `.exe`, on the Python path, and look entries up under that name.
- **`<width>`:** `min(columns, 80)`. This is Python's `shutil.get_terminal_size()`:
  - if `COLUMNS` parses as an integer greater than 0, `columns` is that number;
  - otherwise it's the terminal width of **stdout** (fd 1);
  - if that query fails or returns 0, `columns` is 80.

  Python parses `COLUMNS` with `int()`, which also accepts surrounding whitespace, a `+` sign, `_` separators and non-ASCII digits. `COLUMNS` is keyed in `env`, so if the launcher parses it differently the result is a miss, never a wrong hit.
- **`<tty>`:** `tty` when stdout is a terminal, else `pipe`.

The entry file holds the exact bytes Python wrote to stdout, encoded as UTF-8. Styles (ANSI SGR) are kept only for `tty`. Root help contains non-ASCII characters (the logo), so these UTF-8 rules apply:

- **Python writes an entry only when `sys.stdout.encoding` normalizes to `utf-8`** (`codecs.lookup(encoding).name == "utf-8"`).
- **The bundle runs Python with `-I`**, which ignores every `PYTHON*` variable (`PYTHONIOENCODING`, `PYTHONUTF8`, `PYTHONLEGACYWINDOWSSTDIO` among them). The stdout encoding therefore depends only on the locale (Unix) or the console and code page (Windows). The locale isn't keyed, because Python's own coercion would record values the launcher never sees. Instead, the launcher applies Python's rule itself:
- **On Unix**, serve an entry only when Python's stdout would be UTF-8:
  1. Call `setlocale(LC_CTYPE, "")`.
  2. If it fails, or the resulting `LC_CTYPE` locale is `C` or `POSIX`, serve. That is where Python applies PEP 538 coercion to `C.UTF-8` (`UTF-8` on macOS), or PEP 540 UTF-8 mode when `LC_ALL=C`.
  3. Otherwise serve only when `nl_langinfo(CODESET)` names UTF-8. Normalize it by lowercasing and dropping `-` and `_`, then compare with `utf8`.
  4. In every other case it's a miss: Python's stdout would be in another encoding, and Python would have written no entry.
- **On Windows:**
  - When stdout is a console, Python's encoding is always `utf-8` (PEP 528), and Python writes the text as UTF-16 through `WriteConsoleW`. A launcher serving a `tty` entry to a console must do the same: decode the entry as UTF-8 and write it with `WriteConsoleW`. That is correct whatever the console code page is.
  - When stdout is a pipe or a file, Python's encoding is the ANSI code page. Serve a `pipe` entry only when `GetACP()` is 65001 (UTF-8); otherwise it's a miss.

`<entry>.key.json` sits next to the entry. Ignore any name that starts with `.` and ends in `.tmp`: that's a write in progress.

## Key (`format` 1)

The launcher serves an entry only when every one of the following holds. Anything else is a miss: an unreadable file, invalid JSON, an unknown `format`, or any mismatch.

0. **Reader limits and format evolution.**
   - **Size and shape.** The key file is at most 64 KiB and parses as JSON. `files` has at most 32 items and `env` at most 64 names. The entry file is at most 256 KiB. Anything else is a miss.
   - **Unknown members.** Unknown top-level members are ignored. Python writes exactly the members in the schema; a new top-level member that adds a condition for serving bumps `format`, so an older reader never serves on a key it doesn't fully understand. An unknown member inside a `files` item is a miss.
   - **The schema** is the writer's contract: its `additionalProperties: false` describes what Python writes. Readers don't validate against it.
1. **Header fields.**
   - `format` is 1.
   - `version` is the bundle's deepctl version.
   - `prog` equals the program name the launcher will show, **before** the file-name sanitizing above, with surrounding whitespace stripped. That is the `DEEPCTL_PROG_NAME` value it sets.
   - `width` and `isatty` equal the values computed above.
2. **`executable`** is exactly equal, as a string, to the bundle interpreter's `sys.executable`. It isn't resolved through symlinks, so `…/python3` and `…/python3.12` don't match.
3. **Each item in `files`** is checked with `stat()` on its `path`, following symlinks:
   - **`absent: true`** matches only when the path doesn't exist. Any `stat` error counts as not existing.
   - **Any other item** matches only when `mtime_ns`, `size` and `inode` all equal the current values. `mtime_ns` is in nanoseconds; directories are included, and their `size` is whatever `stat` reports (0 on Windows).
   - **On Windows, `mtime_ns`** is Python's conversion of the last-write FILETIME: `(filetime - 116444736000000000) * 100`, where `filetime` counts 100 ns intervals since 1601-01-01 UTC.
   - **On Windows, `inode`** is Python's `st_ino`, the file ID from `GetFileInformationByHandle(Ex)`. It can be wider than 64 bits on ReFS (Python 3.12 and later), so compare it as an arbitrary-precision integer.
   - **`cwd_relative`** is reserved and never written. A reader that finds it treats the key as a miss.
4. **Each name in `env`** has the same value in the launcher's environment, and `null` means the variable must be unset. On Windows, look the names up case-insensitively, as the OS does. A variable that isn't listed doesn't matter. `DEEPCTL_PROG_NAME` and `DEEPCTL_HELP_CACHE` are never listed: the launcher sets them, and `prog` already records the name.
5. **`text_sha256`** (lowercase hex) equals the SHA-256 of the entry bytes the launcher read. This binds the text to its key: Python writes the text first and the key second, so text from another run under this key is a miss and is never served.

A bundle install (`-I`, no user site, `HOME` is the real home) lists about 10 files and 18 environment variables. Python writes no key over the limits (32 files, 64 variables), so an install with a long `sys.path` (an editable install, say) gets no entry and always takes the Python path.

**Paths and values that aren't valid Unicode.**
- **Unix:** a path or environment value with bytes that don't decode under the filesystem encoding reaches Python as a lone surrogate (`\udcXX`).
- **Windows:** an environment value or path holding an unpaired UTF-16 surrogate does the same.

Either way, Python's strict UTF-8 encode of the key fails and no entry is written, so every key is strict UTF-8 JSON. A launcher compares paths and values as UTF-8 strings. A launcher-side value that isn't valid UTF-8 (on Windows, valid UTF-16) is a mismatch.

**Serving.** On a hit, the launcher writes the entry's bytes to stdout exactly as they are, adding nothing (the text already ends with a newline). It writes nothing to stderr and exits 0. On a Windows console, it follows the UTF-8 rule above.

On a miss, the launcher runs Python with `DEEPCTL_HELP_CACHE=1`.

## When Python writes an entry

Python writes an entry only when all of these hold:

1. `DEEPCTL_HELP_CACHE=1` is in the environment. The launcher sets it only on a miss for argv `["--help"]` or `["-h"]`; Python accepts only `--help`, so the launcher should pass `--help` for both. Python removes the variable at startup, so nothing dg starts inherits it.
2. argv is exactly `["--help"]`.
3. The run is clean: exit 0, and nothing written to stderr.
4. It isn't an AI agent session (`is_ai_agent()`).
5. `DEEPCTL_EAGER_LOAD` isn't `1`.
6. stdout's encoding normalizes to `utf-8`.
7. The key's inputs (`executable`, `files`, `env`), taken before the help is rendered, still hold when it is written. A change during the run (a config edit, for example) means no entry.
8. The key holds no lone surrogate (see above).
9. The key lists at most 32 files and 64 environment variables, and the text is at most 256 KiB.

Writes are atomic. The data goes to a `mkstemp` file (`.<random>.tmp`, mode 0600) in the same directory and is then renamed into place: the text first, then the key.

If the entry and key on disk are already byte-equal to the new ones, nothing is written. Entries for other versions are pruned, as are `.tmp` files older than an hour.

Root `--help` reads no project file (`deepgram.yaml`), so no project-file warning is ever part of it.
