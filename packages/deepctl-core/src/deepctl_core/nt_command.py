"""One command line that cmd, PowerShell and Git Bash all run the same way.

On Windows a ``next_step`` is pasted into whichever shell the agent has:
cmd, Windows PowerShell 5.1, PowerShell 7 or Git Bash. They share no
quoting syntax, so :func:`join` uses the small subset they read alike: a
token from :data:`_BARE` as is, any other token in double quotes, and
``None`` for a token that no form passes through all four unchanged.

Typed into any of the four, the line gives the program the original
tokens. An agent may also wrap it once in another shell without quoting it
again: ``cmd /c "..."``, ``powershell -Command "..."``, ``pwsh -Command
"..."`` or ``'...'``, ``bash -c "..."`` or ``'...'``. The program may then get other
arguments, but no token runs code or creates a file, whatever files are
in the current directory: the characters that would (:data:`_REFUSED`,
globs among them) are never in a line, and the program has no ``=``.

Pure functions only (no ``os.name``): callers decide when to use them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Unquoted, these characters mean the same in every shell named above.
_BARE = re.compile(r"[A-Za-z0-9_+=:./-]+")

# A double-quoted token holding one of these is changed by at least one of
# the shells: " ends the string; % expands in cmd; $ and ` expand in
# PowerShell and Git Bash; ! expands in an interactive Bash and in cmd /v.
# The rest run code once a wrapper strips or splits the double quotes, as
# in cmd /c "...", powershell -Command "..." or bash -c "...": & | < > ^
# in cmd; ; ( ) { } in PowerShell (@{a=cmd} runs cmd); ; in Bash; ' ends
# the agent's own single quotes in bash -c '...' or -Command '...'; and
# * ? [ are globs once Git Bash strips the quotes (bash -c "...", pwsh
# -Command "..."), so a file named "x $(cmd)" in the directory runs cmd.
_REFUSED = frozenset("\"%$`!&|<>^;(){}'*?[")

# Git Bash's MSYS path conversion rewrites arguments it takes for POSIX
# paths, quoted or not: /x becomes C:/Program Files/Git/x, //x becomes /x,
# .:/x a Windows path list. A path may start at the token's start or after
# = @ , ; and leading symbols are skipped.
_SLASH_AT_START = re.compile(r"[^A-Za-z0-9/\\:.-]*/")
_SLASH_AFTER = re.compile(r"[=@,;][^A-Za-z0-9/\\:.-]*/")
# //host/share/... is left alone.
_SHARE = re.compile(r"//[^/]+/[^/]")
# A dash where a path may start, then a slash: -I/x, --dir=/x, a=-b/c.
_DASH_THEN_SLASH = re.compile(r"(?:^|[=@,;])[^A-Za-z0-9/\\:.-]*-.*/")
# Except --name=value whose value is a URL, a drive path or a relative path:
# --base-url=https://h/v1, --dir=C:/x, --out-file=out/a.wav, --dir=../x.
_LONG_OPTION = re.compile(r"--[A-Za-z0-9][A-Za-z0-9-]*=(.*)")
_OPTION_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|[A-Za-z]:/|\.\.?/|[A-Za-z0-9]")
# With a slash in the token, a colon may make it a path list (x/:.), unless
# it is a drive (C:/) or a URL's scheme or port (https://h:443/).
_DRIVE = re.compile(r"(?:^|[=@,;])[A-Za-z]$")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+://")
_PORT = re.compile(r":[0-9]+(?:/|$)")


def forward_slashes(path: str) -> str:
    r"""``C:\Users\me\app`` as ``C:/Users/me/app``; ``\\host\share\x`` as
    ``//host/share/x``. The ``\\?\`` prefix is dropped first. Every shell
    passes forward slashes unchanged, and Windows accepts them in a path.
    """
    from pathlib import PureWindowsPath

    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return PureWindowsPath(path).as_posix()


def needs_quotes(token: str) -> bool:
    """Whether ``token`` must be in double quotes.

    PowerShell splits an unquoted ``-a.b`` or ``-x/y`` in two, and reads an
    unquoted ``-x:`` as a parameter missing its argument; ``--a.b`` is kept
    whole.
    """
    if _BARE.fullmatch(token) is None:
        return True
    return (
        token.startswith("-")
        and not token.startswith("--")
        and ("." in token or "/" in token or token.endswith(":"))
    )


def refused(token: str) -> bool:
    """Whether no form of ``token`` reaches the program unchanged from every
    shell, quoted or not."""
    if not token or not token.isascii():
        # PowerShell 5.1 drops an empty argument; PowerShell ends a string
        # at a typographic quote; code pages differ between shells.
        return True
    if any(ch < " " or ch == "\x7f" or ch in _REFUSED for ch in token):
        return True
    if token.endswith("\\") or "\\\\" in token:
        # A backslash before the closing quote escapes it; Git Bash
        # collapses a doubled one.
        return True
    return _msys_rewrites(token)


def _msys_rewrites(token: str) -> bool:
    """Whether Git Bash may rewrite ``token`` as a path (erring towards yes)."""
    if "/" not in token:
        return False
    option = _LONG_OPTION.fullmatch(token)
    if option and _OPTION_VALUE.match(option[1]):
        # --name=value: Git Bash checks value as a path of its own.
        return _msys_rewrites(option[1])
    if _DASH_THEN_SLASH.search(token):
        return True
    if _SLASH_AT_START.match(token) and not _SHARE.match(token):
        return True
    if _SLASH_AFTER.search(token):
        return True
    url = _URL.match(token)
    for index, ch in enumerate(token):
        if ch != ":" or _DRIVE.search(token, 0, index):
            continue
        if url and (index == url.end() - 3 or _PORT.match(token, index)):
            continue
        return True
    return False


def split(line: str) -> list[str] | None:
    """The tokens :func:`join` built ``line`` from, or ``None`` when it is
    not in that form."""
    tokens: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == '"':
            end = line.find('"', i + 1)
            if end < 0:
                return None
            tokens.append(line[i + 1 : end])
            i = end + 1
        else:
            end = line.find(" ", i)
            end = len(line) if end < 0 else end
            word = line[i:end]
            if not word or '"' in word:
                return None
            tokens.append(word)
            i = end
        if i < len(line):
            if line[i] != " " or i + 1 == len(line):
                return None
            i += 1
    return tokens


def join(tokens: Sequence[str]) -> str | None:
    """``tokens`` as one command line, or ``None``.

    ``tokens[0]`` is the program and must need no quotes: PowerShell reads
    a quoted first word as a string, not a command. Nor may it hold ``=``:
    cmd ends a command name there, so ``D:/p=1/dg.exe`` runs ``D:/p.bat``.
    """
    if not tokens:
        return None
    words: list[str] = []
    for index, token in enumerate(tokens):
        if refused(token) or (index == 0 and "=" in token):
            return None
        if needs_quotes(token):
            if index == 0:
                return None
            words.append(f'"{token}"')
        else:
            words.append(token)
    line = " ".join(words)
    return line if split(line) == list(tokens) else None
