"""Unit tests for the bench harness (no Docker). Run: python -m pytest bench/tests

Set BENCH_TARGETS_MD to the targets document (a markdown file with a
"### 6.1 Targets table") to also diff gates.json against it; without it that
test is skipped.
"""

import json
import os
import re
import sys

import pytest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BENCH)
sys.path.insert(0, os.path.join(BENCH, "lib"))

import check  # noqa: E402
import measure  # noqa: E402

GATES = check.load_gates()
COMP = json.load(open(os.path.join(BENCH, "competitors.json")))


def results(version, rows, extra=None):
    doc = {
        "subjects": {
            "dg": {
                "version": version,
                "rows": {k: {"value": v} for k, v in rows.items()},
            }
        }
    }
    for name, r in (extra or {}).items():
        doc["subjects"][name] = {"rows": {k: {"value": v} for k, v in r.items()}}
    return doc


def by_id(ev):
    return {e["id"]: e for e in ev}


# ----------------------------------------------------------------- gates.json
def test_gate_ids_unique_and_fields():
    ids = [g["id"] for g in GATES["gates"]]
    assert len(ids) == len(set(ids))
    for g in GATES["gates"]:
        assert g["op"] in ("<=", ">=", "==")
        assert g["kind"] in ("timing", "deterministic", "size", "count", "resource")
        check.vtuple(g["since"])
        assert g["target_text"]


def _numbers(text):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text.replace("`", ""))]


def test_target_text_carries_the_gate_value():
    """Each T-gate's value appears in the quoted target text (unit-converted)."""
    for g in GATES["gates"]:
        if not g["target"].startswith("T") or g["unit"] == "bool":
            continue
        nums = _numbers(g["target_text"])
        v = float(g["value"])
        if " s " in g["target_text"] + " " and g["unit"] == "ms":
            nums += [n * 1000 for n in nums]
        assert v in nums, (g["id"], g["value"], g["target_text"])


def _section6_rows(md):
    start = md.index("### 6.1 Targets table")
    end = md.index("### 6.2", start)
    rows = {}
    for line in md[start:end].splitlines():
        m = re.match(r"^\| (T\d+) \|", line)
        if m:
            rows[m.group(1)] = line.replace("**", "")
    return rows


@pytest.mark.skipif(
    not os.environ.get("BENCH_TARGETS_MD"),
    reason="BENCH_TARGETS_MD not set",
)
def test_gates_match_targets_document():
    rows = _section6_rows(open(os.environ["BENCH_TARGETS_MD"]).read())
    for g in GATES["gates"]:
        if g["target"].startswith("T"):
            assert g["target"] in rows, g["id"]
            assert g["target_text"].replace("**", "") in rows[g["target"]], (
                g["id"],
                g["target_text"],
            )
    # every target row whose method is a bench row has at least one gate
    bench_methods = (
        "hyperfine",
        "mcp_all",
        "mcp_probe",
        "bench ",
        "exp_output",
        "exp_prerecorded",
        "size report",
    )
    gated = {g["target"] for g in GATES["gates"]}
    for t, line in rows.items():
        method = line.rstrip(" |").rsplit("|", 1)[-1]
        if any(k in method for k in bench_methods):
            assert t in gated, "%s is measured by the bench but has no gate" % t


# ----------------------------------------------------------------- evaluation
def test_since_controls_activation():
    rows = {"egress_version": 3}
    _, ev = check.evaluate(results("0.4.0", rows), GATES)
    assert by_id(ev)["T4.version"]["outcome"] == "pending"
    _, ev = check.evaluate(results("0.5.0", rows), GATES)
    assert by_id(ev)["T4.version"]["outcome"] == "BLOCK"
    _, ev = check.evaluate(results("0.5.0", {"egress_version": 0}), GATES)
    assert by_id(ev)["T4.version"]["outcome"] == "ok"


def test_prerelease_sorts_below_final():
    assert check.vtuple("1.0.0rc1") < check.vtuple("1.0.0")
    assert check.vtuple("0.5.1") > check.vtuple("0.5.0")
    assert check.vtuple("v0.4.0") == check.vtuple("0.4.0")


def test_timing_gates_are_report_only_until_blocking():
    doc = results("0.5.0", {"version_warm": 45.0})
    _, ev = check.evaluate(doc, GATES)
    assert by_id(ev)["T1.python"]["outcome"] == "report"
    g = json.loads(json.dumps(GATES))
    for x in g["gates"]:
        if x["id"] == "T1.python":
            x["blocking"] = True
    _, ev = check.evaluate(doc, g)
    assert by_id(ev)["T1.python"]["outcome"] == "BLOCK"


def test_missing_row_fails_an_active_blocking_gate():
    _, ev = check.evaluate(results("0.4.0", {}), GATES)
    e = by_id(ev)["D.norecompile.version"]
    assert e["status"] == "n/a" and e["outcome"] == "BLOCK"


def test_backstop_uses_the_looser_limit():
    doc = {
        "subjects": {
            "dg": {
                "version": "0.5.0",
                "rows": {"egress_version_delta_ms": {"value": 40.0, "limit": 50.0}},
            }
        }
    }
    _, ev = check.evaluate(doc, GATES)
    assert by_id(ev)["T4.backstop"]["status"] == "PASS"


def test_competitor_context_is_a_same_run_ratio():
    doc = results(
        "0.5.0",
        {"version_warm": 20.0},
        extra={"gladia": {"version_warm": 2.0}, "gh": {"version_warm": 19.0}},
    )
    _, ev = check.evaluate(doc, GATES)
    best = by_id(ev)["T1.python"]["best"]
    assert best["name"] == "gladia" and best["ratio"] == 10.0


def test_cli_exit_codes(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(
        json.dumps(
            results(
                "0.4.0",
                {"norecompile_version": 0, "norecompile_help": 0, "norecompile_mcp": 0},
            )
        )
    )
    assert check.main([str(p)]) == 0
    p.write_text(
        json.dumps(
            results(
                "0.4.0",
                {"norecompile_version": 2, "norecompile_help": 0, "norecompile_mcp": 0},
            )
        )
    )
    assert check.main([str(p)]) == 1
    assert check.main(["list"]) == 0


# ----------------------------------------------------------------- paired rule
def pair(base, head, row="mcp_init"):
    return {"rounds": {row: {"base": base, "head": head}}}


def test_pair_detects_plus_5ms_on_a_9ms_row():
    base = [9.0, 9.1, 8.9, 9.2, 9.0, 9.1, 9.0, 8.95, 9.05, 9.1]
    head = [b + 5.0 + (0.1 if i % 2 else -0.1) for i, b in enumerate(base)]
    r = check.pair_eval(pair(base, head), GATES)[0]
    assert r["status"] == "RED" and r["threshold"] == 2.0


def test_pair_tolerates_plus_1ms():
    base = [9.0, 9.1, 8.9, 9.2, 9.0, 9.1]
    r = check.pair_eval(pair(base, [b + 1.0 for b in base]), GATES)[0]
    assert r["status"] == "green"


def test_pair_threshold_is_max_of_pct_mad_floor():
    base = [100.0] * 6
    r = check.pair_eval(pair(base, [b + 9.0 for b in base], row="help_warm"), GATES)[0]
    assert r["threshold"] == 10.0 and r["status"] == "green"
    noisy = [100 + d for d in (0, 30, -30, 30, -30, 15)]
    r = check.pair_eval(pair(base, noisy, row="help_warm"), GATES)[0]
    assert r["threshold"] == 3 * r["mad_diff"]


def test_baseline_diff_is_report_only():
    cur = {
        "subjects": {
            "dg": {"rows": {"version_warm": {"value": 50.0, "unit": "ms", "mad": 0.5}}}
        }
    }
    base = {
        "subjects": {"dg": {"rows": {"version_warm": {"value": 30.0, "unit": "ms"}}}}
    }
    (d,) = check.baseline_diff(cur, base, GATES)
    assert d["slower"] and d["threshold"] == 3.0


# ----------------------------------------------------------------- import policy
@pytest.mark.skipif(sys.version_info < (3, 10), reason="needs sys.stdlib_module_names")
def test_import_allowlist_classification():
    pol = GATES["imports"]
    mods = [
        "encodings",
        "json",
        "json.decoder",
        "deepctl",
        "deepctl.main",
        "_virtualenv",
    ]
    r = measure.classify(mods, pol, "version")
    assert r["allowlist_violations"] == 0 and r["forbidden_hits"] == []
    r = measure.classify(
        mods
        + [
            "deepctl_core",
            "click",
            "asyncio.events",
            "importlib.metadata._meta",
            "threading",
        ],
        pol,
        "version",
    )
    assert set(r["allowlist_violation_sample"]) == {
        "deepctl_core",
        "click",
        "asyncio.events",
        "importlib.metadata._meta",
        "threading",
    }
    assert r["forbidden_hits"] == ["asyncio"]
    assert r["third_party_top_level"] == ["click"]
    r = measure.classify(["rich.traceback", "rich"], pol, "help")
    assert r["forbidden_hits"] == ["rich.traceback"] and not r["allowlist_checked"]


def test_source_compile_regex():
    """`python -v` prints source compiles unquoted and .pyc loads quoted (CPython 3.10-3.14)."""
    assert measure.SOURCE_RE.match(
        "# code object from /usr/lib/python3.12/json/__init__.py"
    )
    assert not measure.SOURCE_RE.match(
        "# code object from '/usr/lib/python3.12/json/__pycache__/__init__.cpython-312.pyc'"
    )
    # a quoted .py pattern never matches a real source compile
    assert not measure.QUOTED_PY_RE.match(
        "# code object from /usr/lib/python3.12/json/__init__.py"
    )


def test_import_time_line_parser():
    m = measure.IMPORT_RE.match("import time:       112 |        340 |   deepctl.main")
    assert m and m.group(2) == "deepctl.main"
    assert not measure.IMPORT_RE.match(
        "import time: self [us] | cumulative | imported package"
    )


# ----------------------------------------------------------------- pins
def test_competitors_are_pinned():
    for name, s in COMP["subjects"].items():
        if s["kind"] == "dg":
            continue
        assert s.get("version"), name
        if s["kind"] == "bin":
            assert re.fullmatch(r"[0-9a-f]{64}", s["sha256"]), name
            assert s["version"] in s["url"], name
        elif s["kind"] == "npm":
            assert re.fullmatch(r"\d+\.\d+\.\d+(-[\w.]+)?", s["version"]), name
        elif s["kind"] == "uv":
            assert re.search(r"==\S+$|@[0-9a-f]{40}$", s["spec"]), name
    for ref in COMP["images"].values():
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), ref
    for group in COMP["groups"].values():
        assert set(group) <= set(COMP["subjects"])


def test_pair_head_failing_is_red_and_blocks_when_blocking():
    base = [9.0] * 6
    r = check.pair_eval(pair(base, [None] * 6), GATES)[0]
    assert r["status"] == "RED" and r["outcome"] == "report"
    g = json.loads(json.dumps(GATES))
    g["regression"]["rows"]["mcp_init"]["blocking"] = True
    r = check.pair_eval(pair(base, [None] * 6), g)[0]
    assert r["outcome"] == "BLOCK"
    r = check.pair_eval(pair([None] * 6, base), g)[0]
    assert r["outcome"] == "BLOCK", "a blocking row with no base values must not pass"
    r = check.pair_eval(pair([None] * 6, base), GATES)[0]
    assert r["status"] == "n/a"


def test_unknown_version_fails_closed(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"subjects": {"dg": {"rows": {}}}}))
    assert check.main([str(p)]) == 1


def test_exit_codes_and_wav_header():
    assert measure.bad_codes([0, 0], [0]) == []
    assert measure.bad_codes([0, 1, 1], [0]) == [1]
    h = measure.wav_header(1044)
    assert len(h) == 44 and h[:4] == b"RIFF" and h[8:12] == b"WAVE"
    assert int.from_bytes(h[40:44], "little") == 1000


def test_subject_not_in_harness_stays_pending():
    """A synthetic 0.5.1 run where every dg row meets its target must not block on
    gates for channels the harness can't build yet (dg-native, dg-npm, dg@<os>)."""
    rows = {}
    for g in GATES["gates"]:
        if g["subject"] == "dg":
            rows[g["row"]] = {"value": g["value"], "limit": g["value"]}
    doc = {"subjects": {"dg": {"version": "0.5.1", "rows": rows}}}
    _, ev = check.evaluate(doc, GATES)
    assert [e["id"] for e in ev if e["outcome"] == "BLOCK"] == []
    e = by_id(ev)["T7.checkpoint"]
    assert e["outcome"] == "pending" and e["note"] == "subject not in harness yet"
    # a subject the harness does know, but that produced nothing, still blocks
    g = json.loads(json.dumps(GATES))
    g["gates"].append(
        {
            "id": "X",
            "target": "T6",
            "subject": "gladia",
            "row": "install_mb",
            "op": "<=",
            "value": 1,
            "unit": "MB",
            "kind": "size",
            "since": "0.1.0",
            "target_text": "x",
        }
    )
    _, ev = check.evaluate(doc, g)
    assert (
        by_id(ev)["X"]["outcome"] == "BLOCK"
        and by_id(ev)["X"]["note"] == "subject not built"
    )


def test_pair_neither_side_measured_is_not_a_head_regression():
    r = check.pair_eval(pair([None] * 4, [None] * 4), GATES)[0]
    assert r["status"] == "n/a" and r["outcome"] == "ok"
    assert r["note"] == "neither side produced values"
    g = json.loads(json.dumps(GATES))
    g["regression"]["rows"]["mcp_init"]["blocking"] = True
    r = check.pair_eval(pair([None] * 4, [None] * 4), g)[0]
    assert r["status"] == "n/a" and r["outcome"] == "BLOCK"


def test_pair_since_uses_the_higher_version(tmp_path):
    doc = {
        "base": {"ref": "b" * 12, "version": "0.5.0"},
        "head": {
            "ref": "h" * 12,
            "version": "0.3.9",
            "rows": {"egress_version": {"value": 3}},
        },
        "rounds": {},
        "rounds_n": 0,
    }
    p = tmp_path / "pair.json"
    p.write_text(json.dumps(doc))
    assert check.main(["pair", str(p)]) == 1


def test_every_gate_subject_is_known_or_planned():
    bad = (
        {g["subject"] for g in GATES["gates"]}
        - check.known_subjects()
        - set(GATES["planned_subjects"])
    )
    assert not bad, bad


def test_unknown_subject_is_an_error(tmp_path):
    g = json.loads(json.dumps(GATES))
    g["gates"][0]["subject"] = "dg "  # a typo must not read as a planned channel
    with pytest.raises(check.UnknownSubject):
        check.evaluate(results("0.5.0", {}), g)
    p, gp = tmp_path / "r.json", tmp_path / "g.json"
    p.write_text(json.dumps(results("0.5.0", {})))
    gp.write_text(json.dumps(g))
    os.environ["BENCH_GATES"] = str(gp)
    try:
        assert check.main([str(p)]) == 2
        assert check.main(["list"]) == 2
    finally:
        del os.environ["BENCH_GATES"]
