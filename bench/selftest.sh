#!/bin/sh
# Harness self-test against a fake CLI (bench/tests/fake). Needs Docker only.
#  1. A/A: fake vs fake, paired          -> every row green
#  2. +1 ms on the MCP row               -> green (below the 2 ms floor)
#  3. +5 ms on the ~9 ms MCP row         -> red, and exit 1 once the row is blocking
#  4. a fake that exits 1 fast            -> red (a failed run is never "faster")
#  4b. the same fake, full run at 0.5.0   -> import and no-recompile gates fail on the exit code
#  5. clean fake at 0.6.0, full run       -> MCP/egress/import rows measured, parsers saw real lines
#  6. fake that attempts egress, imports a forbidden module and compiles from source, at 0.5.0
#                                          -> the zero-egress, forbidden-module and no-recompile gates block
#  7. paired run, head compiles a module once into a writable dir
#                                          -> no-recompile sees it: deterministic rows run before warm rounds
set -eu
B=$(cd "$(dirname "$0")" && pwd)
OUT=${SELFTEST_OUT:-$B/out/selftest-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$OUT"
PY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["images"]["python"])' "$B/competitors.json")
check() { docker run --rm --network none -v "$B:/bench:ro" -v "$OUT:/o" -e BENCH_GATES="${GATES:-/bench/gates.json}" "$PY" python3 /bench/check.py "$@"; }
py() { docker run --rm --network none -v "$B:/bench:ro" -v "$OUT:/o" "$PY" python3 -c "$@"; }
fail() { echo "SELFTEST FAIL: $*" >&2; exit 1; }

echo "== 1. A/A"
python3 "$B/bench.py" pair --base fake: --head fake: --rows mcp_init,version_warm --rounds 6 --out "$OUT/aa" --skip-deterministic >/dev/null
check pair /o/aa/pair.json --json /o/aa/check.json >/dev/null || fail "A/A exited non-zero"
py 'import json; r=json.load(open("/o/aa/check.json"))["pair"]; assert all(x["status"]=="green" for x in r), r' || fail "A/A has a red row"

echo "== 2. +1 ms"
python3 "$B/bench.py" pair --base fake: --head fake:delay=1 --rows mcp_init --rounds 6 --out "$OUT/plus1" --skip-deterministic >/dev/null
check pair /o/plus1/pair.json --json /o/plus1/check.json >/dev/null || fail "+1 ms exited non-zero"
py 'import json; r=json.load(open("/o/plus1/check.json"))["pair"]; assert r[0]["status"]=="green", r' || fail "+1 ms went red"

echo "== 3. +5 ms"
python3 "$B/bench.py" pair --base fake: --head fake:delay=5 --rows mcp_init --rounds 6 --out "$OUT/plus5" --skip-deterministic >/dev/null
check pair /o/plus5/pair.json --json /o/plus5/check.json >/dev/null || fail "+5 ms report-only run exited non-zero"
py 'import json; r=json.load(open("/o/plus5/check.json"))["pair"]; assert r[0]["status"]=="RED", r' || fail "+5 ms not detected"
py 'import json; g=json.load(open("/bench/gates.json")); g["regression"]["rows"]["mcp_init"]["blocking"]=True; json.dump(g, open("/o/gates-blocking.json","w"))'
if GATES=/o/gates-blocking.json check pair /o/plus5/pair.json >/dev/null; then fail "+5 ms on a blocking row did not exit 1"; fi

echo "== 4. fails fast"
python3 "$B/bench.py" pair --base fake: --head fake:fail=true --rows version_warm --rounds 4 --out "$OUT/failfast" --skip-deterministic >/dev/null
check pair /o/failfast/pair.json --json /o/failfast/check.json >/dev/null || fail "fail-fast report-only run exited non-zero"
py 'import json; r=json.load(open("/o/failfast/check.json"))["pair"]; assert r[0]["status"]=="RED", r' || fail "fail-fast head not red"
py 'import json; g=json.load(open("/bench/gates.json")); g["regression"]["rows"]["version_warm"]["blocking"]=True; json.dump(g, open("/o/gates-blocking-v.json","w"))'
if GATES=/o/gates-blocking-v.json check pair /o/failfast/pair.json >/dev/null; then fail "fail-fast on a blocking row did not exit 1"; fi

echo "== 4b. fails fast, full run at 0.5.0"
python3 "$B/bench.py" run --ref fake:fail=true,version=0.5.0 --subjects dg --quick --cold-runs 0 --skip-data --out "$OUT/crash" >/dev/null
if check /o/crash/results.json --json /o/crash/check.json >/dev/null; then fail "crashing fake passed the gates"; fi
py '
import json
e = {x["id"]: x for x in json.load(open("/o/crash/check.json"))["gates"]}
for g in ("D.norecompile.version", "D.forbidden.version", "D.allowlist.version"):
    assert e[g]["outcome"] == "BLOCK" and "exit code" in e[g]["note"], e[g]
' || fail "crashing fake gate outcomes"

echo "== 5. clean fake, full run"
python3 "$B/bench.py" run --ref fake:version=0.6.0 --subjects dg --quick --cold-runs 2 --skip-data --out "$OUT/clean" >/dev/null
py '
import json
r = json.load(open("/o/clean/results.json"))["subjects"]["dg"]["rows"]
want = {"mcp_tools": 10, "mcp_annotations_pct": 100.0, "mcp_output_schema_pct": 100.0, "mcp_offline_clean": True,
        "egress_version": 0, "egress_help": 0, "egress_subhelp": 0, "egress_mcp": 0,
        "imports_version_allowlist": 0, "imports_mcp_allowlist": 0, "imports_version": 0,
        "norecompile_version": 0, "norecompile_mcp": 0}
bad = {k: (r.get(k) or {}).get("value") for k, v in want.items() if (r.get(k) or {}).get("value") != v}
assert not bad, bad
for k in ("imports_version", "imports_help", "imports_subhelp", "imports_mcp"):
    assert r[k]["modules"] > 0, (k, r[k])  # the import-time parser saw real lines
for k in ("norecompile_version", "norecompile_help", "norecompile_mcp"):
    assert r[k]["pyc_loads"] > 0, (k, r[k])  # the verbose parser saw real lines
for k in ("version_warm", "help_warm", "subhelp_warm", "mcp_init", "mcp_list", "help_cold"):
    assert isinstance(r[k]["value"], float), (k, r[k])
assert r["help_cold"]["n"] == 2
' || fail "clean fake rows"

echo "== 6. dirty fake at 0.5.0"
python3 "$B/bench.py" run --ref fake:egress=true,imports=asyncio,nopyc=true,version=0.5.0 --subjects dg --quick --cold-runs 0 --skip-data --out "$OUT/dirty" >/dev/null
if check /o/dirty/results.json --json /o/dirty/check.json >/dev/null; then fail "egress gate did not block"; fi
py '
import json
e = {x["id"]: x for x in json.load(open("/o/dirty/check.json"))["gates"]}
for g in ("T4.version", "D.forbidden.version", "D.allowlist.version", "D.norecompile.version"):
    assert e[g]["outcome"] == "BLOCK" and e[g]["status"] == "FAIL", e[g]
' || fail "dirty fake gate outcomes"

echo "== 7. paired ordering"
python3 "$B/bench.py" pair --base fake: --head fake:nopyc_writable=true,version=0.4.0 --rows version_warm --rounds 2 --out "$OUT/order" >/dev/null
if check pair /o/order/pair.json --json /o/order/check.json >/dev/null; then fail "source compile on head did not block"; fi
py '
import json
h = json.load(open("/o/order/pair.json"))["head"]["rows"]
assert h["norecompile_version"]["value"] == 1, h["norecompile_version"]  # first run in the container
assert h["norecompile_help"]["value"] == 0, h["norecompile_help"]  # cached by then
e = {x["id"]: x for x in json.load(open("/o/order/check.json"))["gates"]}
assert e["D.norecompile.version"]["outcome"] == "BLOCK", e["D.norecompile.version"]
assert [k for k, v in e.items() if v["outcome"] == "BLOCK"] == ["D.norecompile.version"], e
' || fail "deterministic rows did not run first"

echo "SELFTEST PASS ($OUT)"
