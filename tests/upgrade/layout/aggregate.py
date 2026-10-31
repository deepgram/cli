"""Aggregate out/<group>/results.jsonl into out/matrix.md and out/matrix.tsv with a verdict per cell.

Verdict rules (what the 1.0 layout predicts for the cell, not the probe's first guess):
  PASS      dg --help and every subcommand --help load, pip check clean, version as predicted
  PASS*     as PASS, but pip/uv check names only the tombstone(s) the cell itself uninstalled
            (root keeps tombstones as dependencies through 1.x, so this is by design)
  PASS-SDK  the cell installs only deepctl-core 0.x (no CLI), which is what the window predicts
  FAIL      anything else (root cause in report.md)"""
import json
import re
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
GROUPS = ["pip", "siblings", "windows", "uv-latest", "uv-0.8", "pipx", "pipx-pipbackend", "next", "backedge", "rc", "uvx-uv", "uvx-uv08"]


def predicted(cell: str, r: dict) -> str | None:
    """Predicted root version (None = any 0.x, '' = no root expected)."""
    c = cell
    if c.startswith("Y-Y100root"):
        return "counterfactual"
    if "fresh" in c and "install-core" in c and c.startswith(("W-", "Y-")):
        return ""
    if c.startswith("W-"):
        return None  # windows: must stay on 0.x
    if c.startswith("Y-Y100pair"):
        return None  # pair yanked: must not land on 1.0.0
    if c.startswith("Y-Y100root"):
        return "counterfactual"
    if c.startswith("S-I050"):
        return None
    if c.startswith("A4-"):
        return None
    if "-uvx-cached-after-1.0-publish" in c:
        return "any"
    return r.get("expect") or None


def verdict(r: dict) -> tuple[str, str]:
    cell = r["cell"]
    loads = r["n_help_failures"] == 0 and r["n_top_commands"] >= 23
    pv = predicted(cell, r)
    chk = " ".join(r["pip_check"])
    tomb_only = (not r["pip_check_ok"]) and all(
        re.search(r"requires .?deepctl-(cmd-[a-z-]+|shared-utils|telemetry)", line) or line.startswith(("Using Python", "Checked", "Found", "'"))
        for line in r["pip_check"]) and "uninstall" in cell
    if pv == "":
        ok = r["root"] == "absent" and r["core"].startswith("0.") and r["pip_check_ok"]
        return ("PASS-SDK" if ok else "FAIL"), f"core {r['core']} only"
    if pv == "counterfactual":
        return "INFO", f"root={r['root']} core={r['core']} (root-only yank)"
    if pv == "any":
        ver_ok = True
    elif pv is None:
        ver_ok = r["root"].startswith("0.") and not r["core"].startswith("1.")
    else:
        ver_ok = r["root"] == pv and pv in r["dg_version"]
    if loads and ver_ok and r["pip_check_ok"]:
        return "PASS", ""
    if loads and ver_ok and tomb_only:
        return "PASS*", "check names the uninstalled tombstone(s)"
    why = []
    if not loads:
        why.append(f"dg load failure: {(r['help_failures'][0][2] if r['help_failures'] else 'no commands').strip()[:90]}")
    if not ver_ok:
        why.append(f"root {r['root']} (predicted {pv or '0.x'})")
    if not r["pip_check_ok"] and not tomb_only:
        why.append(f"check: {chk[:90]}")
    return "FAIL", "; ".join(why)


rows = []
for g in GROUPS:
    f = OUT / g / "results.jsonl"
    if not f.exists():
        continue
    for line in f.read_text().splitlines():
        if g in ("uv-latest", "uv-0.8") and "-uvx-" in line and (OUT / "uvx-uv" / "results.jsonl").exists():
            continue  # superseded by the uvx-* re-run (first run hit harness timeouts)
        r = json.loads(line)
        v, why = verdict(r)
        rows.append((g, r, v, why))
tsv = ["group\tcell\tverdict\troot\tcore\tsiblings\tdg_version\tcheck_clean\ttop+sub_cmds\thelp_failures\twhy"]
md = ["| Group | Cell | Verdict | root | core | siblings | `pip check` | cmds loaded | Note |", "|---|---|---|---|---|---|---|---|---|"]
for g, r, v, why in rows:
    sib = ",".join(r["sibling_versions"]) if len(r["sibling_versions"]) <= 2 else "mixed 0.x (0.4.0-era)"
    cmds = f"{r['n_top_commands']}+{r['n_sub_commands']}" + (f" ({r['n_help_failures']} fail)" if r["n_help_failures"] else "")
    chk = "clean" if r["pip_check_ok"] else "; ".join(x for x in r["pip_check"] if not x.startswith(("Using Python", "Checked")))[:80]
    tsv.append("\t".join(map(str, [g, r["cell"], v, r["root"], r["core"], sib, r["dg_version"], r["pip_check_ok"], cmds, r["n_help_failures"], why])))
    md.append(f"| {g} | `{r['cell']}` | **{v}** | {r['root']} | {r['core']} | {sib} | {chk} | {cmds} | {(why or r.get('note',''))[:110]} |")
counts = {}
for _, _, v, _ in rows:
    counts[v] = counts.get(v, 0) + 1
(OUT / "matrix.tsv").write_text("\n".join(tsv) + "\n")
(OUT / "matrix.md").write_text(f"Totals: {counts}\n\n" + "\n".join(md) + "\n")
print(counts)
for g, r, v, why in rows:
    if v in ("FAIL", "INFO"):
        print(v, g, r["cell"], "|", why)
