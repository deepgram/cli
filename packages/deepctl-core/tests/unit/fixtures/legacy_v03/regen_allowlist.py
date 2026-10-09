"""Regenerate allowlist.tsv: every deepgram/skills SKILL.md blob deepctl 0.2.16-0.3.2 could write.

Not collected by pytest (no test_ prefix). Run it by hand before release:
    python regen_allowlist.py <clone of deepgram/skills> > allowlist.tsv
Needs `gh` (the activity API gives push times and exposes any force-push) and
network access to pypi.org (release upload times). 0.2.16-0.3.2 downloaded
skills/{api,docs,setup-mcp,starters}/SKILL.md from main at install time and
fell back to its own cached copy (~/.deepctl/skills/repo_cache/<name>.md) when
a download failed; <=0.2.15 fetched skills/mcp, so its cache never holds these.
A blob is listed when main served it after the first writer release was
published. live_releases names the releases that were current on PyPI while main
served it (any 0.2.16 to 0.3.2 install could fetch it).
"""

import hashlib
import json
import subprocess
import sys
import urllib.request

NAMES = ("api", "docs", "setup-mcp", "starters")
WRITERS = ("0.2.16", "0.2.17", "0.2.18", "0.2.19", "0.2.20", "0.2.21", "0.2.22")
WRITERS += ("0.2.23", "0.2.24", "0.2.25", "0.2.26", "0.3.0", "0.3.1", "0.3.2")
# v0.2.27 was tagged but never reached PyPI (twine rejected its metadata); its
# generator is byte-identical to 0.3.x, so it adds no blob and no window.


def run(*cmd: str) -> bytes:
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def main(repo: str) -> None:
    acts = json.loads(
        run(
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/deepgram/skills/activity?ref=refs/heads/main&per_page=100",
        )
    )
    acts = sorted((a for page in acts for a in page), key=lambda a: a["timestamp"])
    bad = [a for a in acts if a["activity_type"] == "force_push"]
    assert not bad, f"force-push on main: {bad}"
    pushed = {a["after"]: a["timestamp"] for a in acts}
    history = run(
        "git", "-C", repo, "rev-list", "--first-parent", "--reverse", "main"
    ).split()
    stray = set(pushed) - {c.decode() for c in history}
    assert not stray, f"pushed tips missing from the clone's history: {stray}"
    # Only pushed tips were ever served; a multi-commit push's inner commits never were.
    history = [c for c in history if c.decode() in pushed]
    pypi = json.load(urllib.request.urlopen("https://pypi.org/pypi/deepctl/json"))[
        "releases"
    ]
    released = {v: min(f["upload_time_iso_8601"] for f in pypi[v]) for v in WRITERS}
    first_writer = released[WRITERS[0]]
    head = history[-1].decode()
    windows: dict[
        tuple[str, str], list[str]
    ] = {}  # (name, sha) -> [bytes, commit, start, end]
    current: dict[str, tuple[str, str] | None] = dict.fromkeys(NAMES)
    for c in map(bytes.decode, history):
        at = pushed[c]
        for n in NAMES:
            r = subprocess.run(
                ["git", "-C", repo, "show", f"{c}:skills/{n}/SKILL.md"],
                capture_output=True,
            )
            key = (
                (n, hashlib.sha256(r.stdout).hexdigest()) if r.returncode == 0 else None
            )
            if current[n] and current[n] != key:
                windows[current[n]][3] = at  # Replaced (or deleted) at this push.
            if key and current[n] != key:
                windows.setdefault(key, [str(len(r.stdout)), c[:12], at, "9999"])
                windows[key][3] = "9999"  # Served again from here.
            current[n] = key
    print(
        f"# deepgram/skills main at {head[:12]}; writer releases 0.2.16-0.3.2 from PyPI;"
        " live_releases: releases that were current on PyPI while main served it"
        " (any 0.2.16 to 0.3.2 install could fetch it)"
    )
    print("skill\tbytes\tsha256\tfirst_commit\tpushed_at\treplaced_at\tlive_releases")
    for (n, sha), (size, c, start, end) in sorted(
        windows.items(), key=lambda kv: (NAMES.index(kv[0][0]), kv[1][2])
    ):
        if end <= first_writer:
            print(
                f"# dropped {n} {size}:{sha}: replaced {end}, before 0.2.16 ({first_writer})",
                file=sys.stderr,
            )
            continue
        live = [
            v
            for v in WRITERS
            if released[v] < end
            and (v == WRITERS[-1] or released[WRITERS[WRITERS.index(v) + 1]] > start)
        ]
        print(
            f"{n}\t{size}\t{sha}\t{c}\t{start}\t{'' if end == '9999' else end}\t{','.join(live)}"
        )


if __name__ == "__main__":
    main(sys.argv[1])
