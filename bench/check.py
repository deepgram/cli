"""Evaluate benchmark results against bench/gates.json. Stdlib only.

  check.py list                                   every gate, its target and since
  check.py results.json [--baseline FILE] [--version V] [--json OUT] [--summary FILE]
  check.py pair pair.json [--json OUT] [--summary FILE]

Exit status: 1 when a blocking gate fails (or, in pair mode, a blocking row
regresses); 0 otherwise. A gate blocks when it is active (since <= version
under test) and either is not a timing gate, or is a timing gate whose
'blocking' flag is set (timing gates stay report-only until the A/A rule in
bench/README.md clears). An active blocking gate whose row is missing fails,
and so does a run whose version under test is unknown.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_gates(path=None):
    with open(path or os.path.join(HERE, "gates.json")) as f:
        return json.load(f)


def load_groups():
    with open(os.path.join(HERE, "competitors.json")) as f:
        return json.load(f)["groups"]


class UnknownSubject(ValueError):
    """A gate names a subject that is neither built by the harness nor planned."""


def check_subjects(gates):
    """Every gate's subject is built by the harness or listed in planned_subjects;
    anything else (a typo) is an error. Returns the planned set."""
    planned = set(gates.get("planned_subjects", []))
    unknown = sorted(
        {g["subject"] for g in gates["gates"]} - known_subjects() - planned
    )
    if unknown:
        raise UnknownSubject(
            "gates name unknown subjects %s (not in competitors.json or planned_subjects)"
            % unknown
        )
    return planned


def known_subjects():
    """Subjects the harness can build. A gate on any other subject (a channel that
    doesn't exist yet) stays pending instead of blocking."""
    with open(os.path.join(HERE, "competitors.json")) as f:
        return set(json.load(f)["subjects"])


def vtuple(v):
    """'0.4.0' -> (0, 4, 0, 1); '1.0.0rc1' -> (1, 0, 0, 0) so a prerelease sorts below the final."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", str(v or "").strip().lstrip("v"))
    if not m:
        raise ValueError("bad version %r" % v)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if m.group(4) else 1)


def compare(op, measured, target):
    if op == "<=":
        return measured <= target
    if op == ">=":
        return measured >= target
    if op == "==":
        return measured == target
    raise ValueError(op)


def median(xs):
    return statistics.median(xs) if xs else None


def mad(xs):
    if not xs:
        return None
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def fmt(v, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        v = round(v, 1) if abs(v) >= 10 else round(v, 2)
    return "%s%s" % (v, (" " + unit) if unit and unit not in ("count", "bool") else "")


def evaluate(results, gates, version=None, groups=None, only_rows=None):
    groups = groups or load_groups()
    subj = results.get("subjects", {})
    version = version or (subj.get(gates["subject"]) or {}).get("version")
    planned = check_subjects(gates)
    out = []
    for g in gates["gates"]:
        s = subj.get(g["subject"]) or {}
        # only an explicitly planned channel may be absent without blocking
        not_in_harness = g["subject"] not in subj and g["subject"] in planned
        row = (s.get("rows") or {}).get(g["row"])
        if only_rows is not None and row is None:
            continue
        measured = None if row is None else row.get("value")
        target = g["value"]
        if (
            row is not None
            and g.get("limit_field")
            and row.get(g["limit_field"]) is not None
        ):
            target = max(target, row[g["limit_field"]])
        active = version is not None and vtuple(g["since"]) <= vtuple(version)
        blocking = active and (g["kind"] != "timing" or bool(g.get("blocking")))
        if measured is None:
            status = "n/a"
            why = (row or {}).get("error") or (
                "subject not in harness yet"
                if not_in_harness
                else ("subject not built" if not s else "row not measured")
            )
        else:
            status = "PASS" if compare(g["op"], measured, target) else "FAIL"
            why = ""
        if not active or (not_in_harness and measured is None):
            outcome = "pending"
        elif status == "PASS":
            outcome = "ok"
        elif blocking:
            outcome = "BLOCK"
        else:
            outcome = "report"
        best = None
        if g.get("vs") and g["vs"] in groups:
            vals = []
            for n in groups[g["vs"]]:
                r = ((subj.get(n) or {}).get("rows") or {}).get(
                    g.get("peer_row", g["row"])
                )
                if (
                    r
                    and isinstance(r.get("value"), (int, float))
                    and not isinstance(r.get("value"), bool)
                ):
                    vals.append((r["value"], n))
            if vals:
                bv, bn = min(vals)
                best = {
                    "name": bn,
                    "value": bv,
                    "ratio": round(measured / bv, 3)
                    if isinstance(measured, (int, float)) and bv
                    else None,
                }
        out.append(
            {
                "id": g["id"],
                "target": g["target"],
                "subject": g["subject"],
                "row": g["row"],
                "op": g["op"],
                "target_value": target,
                "unit": g["unit"],
                "kind": g["kind"],
                "since": g["since"],
                "measured": measured,
                "status": status,
                "active": active,
                "blocking": blocking,
                "outcome": outcome,
                "best": best,
                "note": why,
            }
        )
    return version, out


def baseline_diff(results, baseline, gates):
    """Cross-run comparison against a stored per-runner baseline. Always report-only:
    regressions are judged on same-job paired runs, not on ms from another run."""
    reg = gates["regression"]
    out = []
    for name, s in results.get("subjects", {}).items():
        b = (baseline.get("subjects") or {}).get(name)
        if not b:
            continue
        for row, r in sorted((s.get("rows") or {}).items()):
            br = (b.get("rows") or {}).get(row)
            if not br or r.get("unit") != "ms":
                continue
            v, bv = r.get("value"), br.get("value")
            if (
                not isinstance(v, (int, float))
                or not isinstance(bv, (int, float))
                or isinstance(v, bool)
            ):
                continue
            floor = reg["rows"].get(row, {}).get("floor_ms", 2.0)
            noise = reg["mad_k"] * (r.get("mad") or 0)
            thr = max(reg["pct"] / 100.0 * bv, noise, floor)
            out.append(
                {
                    "subject": name,
                    "row": row,
                    "baseline": bv,
                    "current": v,
                    "delta": round(v - bv, 3),
                    "threshold": round(thr, 3),
                    "slower": (v - bv) > thr,
                }
            )
    return out


def pair_eval(pair, gates):
    reg = gates["regression"]
    rows = []
    for row, sides in sorted(pair["rounds"].items()):
        b, h = sides["base"], sides["head"]
        diffs = [
            hh - bb
            for bb, hh in zip(b, h)
            if isinstance(bb, (int, float)) and isinstance(hh, (int, float))
        ]
        cfg = reg["rows"].get(row, {})
        blocking = bool(cfg.get("blocking"))
        nb = sum(isinstance(x, (int, float)) for x in b)
        nh = sum(isinstance(x, (int, float)) for x in h)
        rounds = max(len(b), len(h), 1)
        if nh * 2 < rounds and nb * 2 < rounds:
            # Neither side measured: nothing regressed, nothing passed either.
            rows.append(
                {
                    "row": row,
                    "status": "n/a",
                    "blocking": blocking,
                    "outcome": "BLOCK" if blocking else "ok",
                    "note": "neither side produced values",
                }
            )
            continue
        if nh * 2 < rounds or nb * 2 < rounds:
            # A side that failed in most rounds is never "no change": head failing
            # is a regression; base failing leaves nothing to compare against.
            side = "head" if nh * 2 < rounds else "base"
            red = side == "head" or blocking
            rows.append(
                {
                    "row": row,
                    "status": "RED" if red else "n/a",
                    "blocking": blocking,
                    "outcome": "BLOCK"
                    if red and blocking
                    else ("report" if red else "ok"),
                    "note": "%s produced values in %d of %d rounds"
                    % (side, nh if side == "head" else nb, rounds),
                }
            )
            continue
        bm = median([x for x in b if isinstance(x, (int, float))])
        d, m = median(diffs), mad(diffs)
        thr = max(reg["pct"] / 100.0 * bm, reg["mad_k"] * m, cfg.get("floor_ms", 2.0))
        red = d > thr
        rows.append(
            {
                "row": row,
                "base_median": round(bm, 3),
                "head_median": round(
                    median([x for x in h if isinstance(x, (int, float))]), 3
                ),
                "median_diff": round(d, 3),
                "mad_diff": round(m, 3),
                "threshold": round(thr, 3),
                "pairs": len(diffs),
                "status": "RED" if red else "green",
                "blocking": bool(cfg.get("blocking")),
                "outcome": "BLOCK"
                if red and cfg.get("blocking")
                else ("report" if red else "ok"),
            }
        )
    return rows


# ----------------------------------------------------------------- output
def gate_table(version, ev):
    lines = [
        "Version under test: **%s**" % version,
        "",
        "| gate | target | measured | best peer (ratio) | since | kind | result |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in ev:
        best = (
            "%s %s (%s×)"
            % (
                e["best"]["name"],
                fmt(e["best"]["value"], e["unit"]),
                e["best"]["ratio"],
            )
            if e["best"]
            else ""
        )
        res = {
            "ok": "PASS",
            "BLOCK": "**FAIL (blocking)**",
            "report": "FAIL (report-only)",
        }.get(e["outcome"])
        if e["outcome"] == "pending":
            res = "pending (%s)" % e["status"]
        note = (" — " + e["note"][:80].replace("\n", " ")) if e["note"] else ""
        lines.append(
            "| %s | %s %s | %s%s | %s | %s | %s%s | %s |"
            % (
                e["id"],
                e["op"],
                fmt(e["target_value"], e["unit"]),
                fmt(e["measured"], e["unit"]),
                note,
                best,
                e["since"],
                e["kind"],
                ""
                if e["kind"] != "timing"
                else (" (blocking)" if e["blocking"] else " (report-only)"),
                res,
            )
        )
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    gates = load_gates(os.environ.get("BENCH_GATES"))
    try:
        return _main(argv, gates)
    except UnknownSubject as e:
        print("ERROR: %s" % e)
        return 2


def _main(argv, gates):
    check_subjects(gates)
    if argv and argv[0] == "list":
        print(
            "| gate | target | subject | row | target | since | kind |\n|---|---|---|---|---|---|---|"
        )
        for g in gates["gates"]:
            print(
                "| %s | %s | %s | %s | %s %s %s | %s | %s |"
                % (
                    g["id"],
                    g["target"],
                    g["subject"],
                    g["row"],
                    g["op"],
                    g["value"],
                    g["unit"],
                    g["since"],
                    g["kind"],
                )
            )
        return 0
    ap = argparse.ArgumentParser()
    if argv and argv[0] == "pair":
        ap.add_argument("mode")
    ap.add_argument("file")
    ap.add_argument("--baseline")
    ap.add_argument("--version")
    ap.add_argument("--json")
    ap.add_argument("--summary")
    a = ap.parse_args(argv)
    doc = json.load(open(a.file))
    report, blocked, out = [], False, {}
    if getattr(a, "mode", None) == "pair":
        pr = pair_eval(doc, gates)
        report += [
            "### Paired base vs head (%s → %s, %d rounds)"
            % (doc["base"]["ref"][:12], doc["head"]["ref"][:12], doc["rounds_n"]),
            "",
            "| row | base | head | median Δ | MAD Δ | threshold | result |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in pr:
            if "base_median" not in r:
                report.append(
                    "| %s | | | | | | %s (%s)%s |"
                    % (
                        r["row"],
                        r["status"],
                        r["note"],
                        "" if r["blocking"] else " (report-only)",
                    )
                )
                blocked |= r.get("outcome") == "BLOCK"
                continue
            report.append(
                "| %s | %s | %s | %+.2f | %.2f | %.2f | %s%s |"
                % (
                    r["row"],
                    fmt(r["base_median"], "ms"),
                    fmt(r["head_median"], "ms"),
                    r["median_diff"],
                    r["mad_diff"],
                    r["threshold"],
                    r["status"],
                    "" if r["blocking"] else " (report-only)",
                )
            )
            blocked |= r["outcome"] == "BLOCK"
        head = {
            "subjects": {
                gates["subject"]: {
                    "version": doc["head"]["version"],
                    "rows": doc["head"]["rows"],
                }
            }
        }
        # `since` follows the higher of base and head, so a PR can't switch gates
        # off by lowering its own version.
        pv = [v for v in (doc["base"].get("version"), doc["head"].get("version")) if v]
        pair_version = a.version or (max(pv, key=vtuple) if pv else None)
        version, ev = evaluate(head, gates, version=pair_version, only_rows=True)
        if version is None:
            print("head version unknown in %s; pass --version" % a.file)
            return 1
        ev = [e for e in ev if e["kind"] != "timing"]
        report += ["", "### Deterministic gates on head", "", gate_table(version, ev)]
        out = {"pair": pr, "gates": ev}
    else:
        version, ev = evaluate(doc, gates, version=a.version)
        if version is None:
            print(
                "version under test unknown (no subject version in %s); pass --version"
                % a.file
            )
            return 1
        report += ["### Gates", "", gate_table(version, ev)]
        out = {"version": version, "gates": ev}
        if a.baseline:
            bd = baseline_diff(doc, json.load(open(a.baseline)), gates)
            slower = [d for d in bd if d["slower"]]
            report += [
                "",
                "### Against baseline %s (report-only; cross-run)"
                % os.path.basename(a.baseline),
                "",
                "%d timing rows compared, %d slower than max(10%%, 3×MAD, floor)."
                % (len(bd), len(slower)),
            ]
            for d in slower:
                report.append(
                    "- %s %s: %s → %s ms (Δ %+.1f, threshold %.1f)"
                    % (
                        d["subject"],
                        d["row"],
                        d["baseline"],
                        d["current"],
                        d["delta"],
                        d["threshold"],
                    )
                )
            out["baseline"] = bd
    blocked |= any(e["outcome"] == "BLOCK" for e in ev)
    text = "\n".join(report)
    print(text)
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(text + "\n")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)
    print(
        "\nRESULT: %s"
        % ("FAIL (blocking gate)" if blocked else "OK (no blocking gate failed)")
    )
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
