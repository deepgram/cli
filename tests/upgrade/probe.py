"""Per-cell probe.

Usage: probe.py CELL_ID PY DG CHECK_CMD [--expect-version V] [--expect-dists N]
                [--expect-all] [--min-commands N] [--need CMD] [--note TEXT]

Records the installed first-party dists, `dg --version`, `pip check` (or the
uv/pipx equivalent), and whether `dg --help` and every command's and
subcommand's `--help` (two levels) load cleanly. Appends a JSON line to
/o/results.jsonl and prints a one-line PASS/FAIL summary.

--expect-version V  the root dist is V and `dg --version` prints V
--expect-dists N    exactly N first-party dists are installed
--expect-all        every first-party dist is at --expect-version (no split install)
--min-commands N    `dg --help` lists at least N top-level commands
"""
import json
import shlex
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

cell, py, dg, check_cmd = sys.argv[1:5]
py, dg = shlex.split(py), shlex.split(dg)
rest = sys.argv[5:]
expect = rest[rest.index("--expect-version") + 1] if "--expect-version" in rest else ""
note = rest[rest.index("--note") + 1] if "--note" in rest else ""
need = rest[rest.index("--need") + 1] if "--need" in rest else ""
expect_dists = int(rest[rest.index("--expect-dists") + 1]) if "--expect-dists" in rest else None
expect_all = "--expect-all" in rest
min_commands = int(rest[rest.index("--min-commands") + 1]) if "--min-commands" in rest else 1
BAD = re.compile(r"Error loading|Failed to load|Traceback|ModuleNotFoundError|ImportError|No module named|Warning: .*load", re.I)


def run(cmd, timeout=int(__import__("os").environ.get("PROBE_TIMEOUT", "60"))):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=isinstance(cmd, str))
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"
    except FileNotFoundError as e:
        return 127, "", str(e)


def commands(help_text: str) -> list[str]:
    out, on = [], False
    for line in help_text.splitlines():
        s = line.strip("│ ").rstrip()
        if re.match(r"^[╭─ ]*Commands", s) or s.startswith("Commands:"):
            on = True
            continue
        if on:
            if not s or s.startswith("╰") or (line and not line.startswith((" ", "│"))):
                if out and (not s or s.startswith("╰")):
                    break
                continue
            m = re.match(r"^([a-z][a-z0-9_-]*)\b", s)
            if m:
                out.append(m.group(1))
    return out


rc, dists, _ = run([*py, "-I", "-c", "import importlib.metadata as m; print('\\n'.join(sorted(f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions() if d.metadata['Name'].lower().replace('_','-').startswith('deepctl'))))"])
dists = dists.split()
root = next((d.split("==")[1] for d in dists if d.lower().startswith("deepctl==")), "absent")
core = next((d.split("==")[1] for d in dists if d.lower().replace("_", "-").startswith("deepctl-core==")), "absent")
sib_versions = sorted({d.split("==")[1] for d in dists if not d.lower().startswith(("deepctl==", "deepctl-core==", "deepctl_core=="))})
crc, cout, cerr = run(check_cmd)
check_ok = crc == 0
check_msg = (cout + cerr).strip().splitlines()
vrc, vout, verr = run([*dg, "--version"])
hrc, hout, herr = run([*dg, "--help"])
top = commands(hout)
failures = []
if hrc != 0 or BAD.search(herr + hout):
    failures.append(("--help", hrc, (herr or hout)[-300:]))
paths = [[c] for c in top]


def help_of(path):
    r, o, e = run([*dg, *path, "--help"])
    return path, r, o, e


with ThreadPoolExecutor(int(__import__("os").environ.get("PROBE_WORKERS", "8"))) as ex:
    level1 = list(ex.map(help_of, paths))
sub_paths = []
for path, r, o, e in level1:
    if r != 0 or BAD.search(e):
        failures.append((" ".join(path), r, (e or o)[-300:]))
    for s in commands(o):
        sub_paths.append(path + [s])
with ThreadPoolExecutor(int(__import__("os").environ.get("PROBE_WORKERS", "8"))) as ex:
    level2 = list(ex.map(help_of, sub_paths))
for path, r, o, e in level2:
    if r != 0 or BAD.search(e):
        failures.append((" ".join(path), r, (e or o)[-300:]))
version_line = (vout or verr).strip().splitlines()[-1] if (vout or verr).strip() else f"rc={vrc}"
all_versions = sorted({d.split("==")[1] for d in dists})
ok = (check_ok and not failures and len(top) >= min_commands and (not need or need in top)
      and (not expect or (root == expect and expect in version_line))
      and (expect_dists is None or len(dists) == expect_dists)
      and (not expect_all or all_versions == [expect]))
rec = {"cell": cell, "ok": ok, "root": root, "core": core, "sibling_versions": sib_versions,
       "n_firstparty_dists": len(dists), "firstparty_versions": all_versions, "dg_version": version_line, "pip_check_ok": check_ok,
       "pip_check": check_msg[:4], "n_top_commands": len(top), "n_sub_commands": len(sub_paths),
       "help_failures": failures[:6], "n_help_failures": len(failures), "expect": expect, "need": need, "note": note,
       "top_commands": top}
with open(__import__("os").environ.get("PROBE_OUT", "/o/results.jsonl"), "a") as f:
    f.write(json.dumps(rec) + "\n")
print(f"{'PASS' if ok else 'FAIL'} {cell}: root={root} core={core} sibs={','.join(sib_versions)} dists={len(dists)} "
      f"version='{version_line}' check={'clean' if check_ok else check_msg[:2]} "
      f"cmds={len(top)}+{len(sub_paths)} help_fail={len(failures)} {failures[:2] if failures else ''}")
