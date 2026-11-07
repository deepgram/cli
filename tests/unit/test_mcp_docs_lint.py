"""Shipped text must not promise `dg mcp` behaviour it doesn't have.

Agents follow docs literally, so this scans the READMEs, the website
(including llms.txt and llms-full.txt), the sources and the help specs
for text that was true of an older plan, not of the shipped command:

- transports other than stdio, in any spelling (`--transport sse`,
  `--transport=http`, `-t sse`, `"--transport", "sse"`, an `/sse` URL
  over http or ws);
- a `--stdout` option `dg mcp` never had;
- an API key on a command line (any value after `--api-key` in a dg
  command) or in an MCP config's args, including args arrays split over
  several lines.

Only the API-key checks have an exemption: `dg login --api-key ...` gives
the key to the command whose job is to store it. The exemption ends at a
shell operator, a backtick or the end of a sentence. Every other check
still applies to that line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_TRANSPORTS = r"(?:sse|http|https|streamable[-_]?http|ws|websocket)\b"
LINE_CHECKS = {
    "non-stdio transport": re.compile(
        r"(?:--transport(?:\s+|=)|(?<![\w-])-t\s+)[\"']?" + _TRANSPORTS,
        re.IGNORECASE,
    ),
    "non-stdio transport in args": re.compile(
        r"[\"'](?:--transport|-t)[\"']\s*,\s*[\"']" + _TRANSPORTS, re.IGNORECASE
    ),
    # The CLI spelling; mcp.client.streamable_http is a module name.
    "streamable-http": re.compile(r"streamable-http", re.IGNORECASE),
    "/sse URL": re.compile(r"\b(?:https?|wss?)://[^\s\"'`<>)]*/sse(?![\w-])"),
    "--stdout": re.compile(r"--stdout\b"),
    "stdio/SSE/HTTP": re.compile(r"stdio\s*/\s*SSE", re.IGNORECASE),
}
# A key value after --api-key on a command line: a placeholder, a variable
# or something key-shaped.
KEY_CHECKS = {
    "--api-key with a value": re.compile(
        r"--api-key(?:\s+|=)[\"']?(?:YOUR|KEY\b|<|\$|sk-|[0-9a-f]{32})",
        re.IGNORECASE,
    ),
    # Any value after --api-key in a dg command: dg, then options and
    # arguments up to --api-key, with no shell operator or backtick between.
    "--api-key on a command line": re.compile(
        r"(?:^|[\s`$(])(?:dg|deepctl|deepgram)(?:\s+[^\s|;&`]+)*?\s+"
        r"--api-key(?:\s+|=)[\"']?[\w$<{]"
    ),
}
# The dg login command the key is given to: it ends at a shell operator, a
# backtick or the end of a sentence.
LOGIN = re.compile(r"\bdg\s+login\b(?:(?![.!?](?:\s|$))[^|;&`\n])*$")
# An MCP config's args array, however it is wrapped.
ARGS = re.compile(r"[\"']args[\"']\s*:\s*\[([^\]]*)\]", re.DOTALL)
ARGS_CHECKS = {
    "--api-key in MCP args": re.compile(r"[\"']--api-key(?:=[^\"']*)?[\"']"),
    "non-stdio transport in MCP args": re.compile(
        r"[\"'](?:--transport|-t)[\"']\s*,\s*[\"']" + _TRANSPORTS
        + r"|[\"']--transport=" + _TRANSPORTS,
        re.IGNORECASE,
    ),
}

SKIP_DIRS = {"node_modules", "dist", ".astro", "__pycache__", "tests"}
# Source that names a transport only to refuse it: the standalone entry point
# (`python -m deepctl_cmd_mcp`) still parses the names an old MCP config may
# use, so it prints E_MCP_TRANSPORT instead of an argparse error.
REFUSES = {
    ("packages/deepctl-cmd-mcp/src/deepctl_cmd_mcp/__main__.py", "streamable-http"),
}
TEXT_SUFFIXES = {".md", ".txt", ".py", ".astro", ".ts", ".tsx", ".js", ".json"}


def _files() -> list[Path]:
    roots = [REPO / "web" / "public", REPO / "web" / "src", REPO / "src"]
    files = sorted(REPO.glob("README*")) + sorted(REPO.glob("packages/*/README*"))
    roots += sorted(p for p in REPO.glob("packages/*/src") if p.is_dir())
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix in TEXT_SUFFIXES
                and not SKIP_DIRS.intersection(path.relative_to(REPO).parts)
            ):
                files.append(path)
    return files


def check_text(text: str) -> list[tuple[int, str]]:
    """(line number, check name) for every untrue MCP claim in ``text``."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in LINE_CHECKS.items():
            if pattern.search(line):
                hits.append((number, label))
        for label, pattern in KEY_CHECKS.items():
            for match in pattern.finditer(line):
                if not LOGIN.search(line[: line.index("--api-key", match.start())]):
                    hits.append((number, label))
                    break
    for match in ARGS.finditer(text):
        number = text.count("\n", 0, match.start()) + 1
        for label, pattern in ARGS_CHECKS.items():
            if pattern.search(match.group(1)):
                hits.append((number, label))
    return hits


def _hits() -> list[str]:
    hits = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        rel = path.relative_to(REPO)
        for number, label in check_text(text):
            if (rel.as_posix(), label) in REFUSES:
                continue
            hits.append(f"{rel}:{number}: {label}: {lines[number - 1].strip()}")
    return hits


def test_scans_the_shipped_text() -> None:
    names = {p.relative_to(REPO).as_posix() for p in _files()}
    assert "README.md" in names
    assert "packages/deepctl-cmd-mcp/README.md" in names
    assert "packages/deepctl-cmd-mcp/src/deepctl_cmd_mcp/command.py" in names
    if (REPO / "web" / "public" / "llms-full.txt").exists():
        assert "web/public/llms-full.txt" in names


def test_no_untrue_mcp_text() -> None:
    assert _hits() == []


@pytest.mark.parametrize(
    "text",
    [
        "dg mcp --transport sse --port 8000",
        "dg mcp --transport=sse",
        "dg mcp -t sse",
        "dg mcp --transport http",
        "dg mcp --transport streamable-http",
        "dg mcp --transport 'sse'",
        '"args": ["mcp", "--transport", "sse"]',
        '"args": ["mcp", "-t", "http"]',
        '"args": ["mcp", "--transport=sse"]',
        '"args": ["deepctl", "--api-key", "YOUR_KEY", "mcp"]',
        '"args": [\n  "deepctl",\n  "--api-key",\n  "abc",\n  "mcp"\n]',
        '"args": ["deepctl", "--api-key=abc", "mcp"]',
        "dg --api-key=KEY mcp",
        "dg --api-key YOUR_KEY mcp",
        'dg --api-key "$DEEPGRAM_API_KEY" mcp',
        "Add to your AI editor: http://127.0.0.1:8000/sse",
        "dg mcp --stdout",
        "MCP server mode: `dg mcp` (stdio/SSE/HTTP)",
        "dg login --api-key KEY && dg mcp --transport sse",
        "dg login; dg --api-key=KEY mcp",
        "ws://127.0.0.1:8000/sse",
        "wss://example.com/mcp/sse",
        "dg --api-key abc123 mcp",
        "deepctl --profile x --api-key abc123 mcp",
        "Run `dg login`, then `dg --api-key abc123 mcp`",
        "Run dg login. Then run dg --api-key abc123 mcp",
    ],
)
def test_catches_the_old_text(text: str) -> None:
    assert check_text(text), text


@pytest.mark.parametrize(
    "text",
    [
        "dg mcp",
        "dg mcp --transport stdio",
        '"args": ["mcp", "--transport", "stdio"]',
        '"args": ["deepctl", "mcp"]',
        "a `--transport` other than `stdio` exits with an error",
        "Use stdio: remove --transport from your MCP config",
        "dg login --api-key YOUR_API_KEY",
        "dg login --profile staging --api-key YOUR_KEY",
        '_SECRET_OPTIONS = ("--api-key", "-k")',
        'if token.startswith("--api-key="):',
        "https://developers.deepgram.com/docs/sse-handling",
        "from mcp.client.streamable_http import streamablehttp_client",
        "dg login --api-key abc123",
        "Pass the key with dg's --api-key flag, or run dg login.",
        "ws://127.0.0.1:8000/v1/listen",
    ],
)
def test_leaves_true_text_alone(text: str) -> None:
    assert check_text(text) == [], text
