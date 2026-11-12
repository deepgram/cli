"""Build one PEP 503 index state (simulates what PyPI shows at a moment in the release).
Usage: python -I mkidx.py OUTDIR POOL [--yank PREFIX ...] SRC_GLOB [SRC_GLOB ...]
Each SRC_GLOB is a directory, optionally with ':' filter, e.g. /w/wheels-1.0.0:-deepctl-1.0.0
  ':-prefix' drops files whose normalized name starts with prefix (e.g. root not yet uploaded)
  ':+prefix' keeps only those files.
Files are linked as /pool/<file>; POOL must contain them (they are symlinked there).
--yank 'deepctl-1.0.0' marks matching files data-yanked (PEP 592)."""
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

args = sys.argv[1:]
out, pool = Path(args.pop(0)), Path(args.pop(0))
yanks = []
while args and args[0] == "--yank":
    args.pop(0)
    yanks.append(args.pop(0))


def key(fname: str) -> str:
    # normalized "<project>-<version>" for filter matching
    stem = fname[:-4] if fname.endswith(".whl") else fname
    name, ver = stem.split("-")[:2]
    return re.sub(r"[-_.]+", "-", name).lower() + "-" + ver


projects = defaultdict(set)
for spec in args:
    d, _, filt = spec.partition(":")
    for f in os.listdir(d):
        if not f.endswith((".whl", ".tar.gz")):
            continue
        k = key(f)
        if filt.startswith("-") and any(k.startswith(p) for p in filt[1:].split(",")):
            continue
        if filt.startswith("+") and not any(k.startswith(p) for p in filt[1:].split(",")):
            continue
        tgt = pool / f
        if not tgt.exists():
            tgt.symlink_to(Path(d) / f)
        proj = k.rsplit("-", 1)[0]
        projects[proj].add(f)
(out / "simple").mkdir(parents=True, exist_ok=True)
for proj, files in projects.items():
    (out / "simple" / proj).mkdir(exist_ok=True)
    links = []
    for f in sorted(files):
        y = ' data-yanked="upgrade matrix: yanked"' if any(key(f) == p or key(f).startswith(p + "-") or key(f) == p for p in yanks) else ""
        links.append(f'<a href="/pool/{f}"{y}>{f}</a><br/>')
    (out / "simple" / proj / "index.html").write_text("<!DOCTYPE html><html><body>\n" + "\n".join(links) + "\n</body></html>\n")
(out / "simple" / "index.html").write_text("<html><body>" + "".join(f'<a href="{p}/">{p}</a>' for p in sorted(projects)) + "</body></html>")
print(f"{out}: {len(projects)} projects, yanked={yanks}", file=sys.stderr)
