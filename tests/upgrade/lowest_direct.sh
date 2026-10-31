#!/bin/bash
# One environment with every third-party dependency at the lowest version the
# lockstep set allows, then the CLI and the unit tests in it.
#
#   tests/upgrade/lowest_direct.sh DIST_DIR [PYTHON] [install|test|all]   (repo root)
#
# DIST_DIR holds `uv build --all-packages --wheel` output. Every package
# declares `deepctl==V`, so any install pulls the whole closure: what a user
# can actually get at the floors is one environment, not 30. Its direct
# requirements are `deepctl==V` plus every third-party requirement of the 30
# pyprojects, resolved with `--resolution lowest-direct` (transitive
# dependencies stay at their highest), wheels only (`--no-build`).
# Then: `pip check`, `dg --help` and every command's and subcommand's --help
# (tests/upgrade/probe.py), and the unit tests, one suite per process. A declared floor below the
# version the closure actually resolves is listed: it is never tested and can
# be raised to match. The `install` and `test` phases can run separately (the
# test phase needs no network).
set -uo pipefail
DIST=$(cd "$1" && pwd); PY=${2:-python3}; PHASE=${3:-all}
HERE=$(cd "$(dirname "$0")" && pwd)
ENV=${LOWEST_ENV:-${TMPDIR:-/tmp}/lowest-direct}
export DEEPCTL_TELEMETRY_DISABLED=1 DEEPCTL_TELEMETRY_DSN=http://k@127.0.0.1:9/1
# tests/unit/test_dependency_allowlist.py checks its floors-only entries here.
export DEEPCTL_LOWEST_DIRECT=1
TEST_TOOLS="pytest pytest-asyncio pytest-mock pytest-timeout responses"

if [ "$PHASE" != test ]; then
  rm -rf "$ENV"; mkdir -p "$ENV"
  uv run -q --no-project --python "$PY" --with "tomli>=2.0; python_version < '3.11'" \
    python - "$ENV/requirements.txt" "$ENV/floors.tsv" <<'PYEOF'
import re, sys
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
reqs, floors = [], []
version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
for p in [Path("pyproject.toml"), *sorted(Path("packages").glob("*/pyproject.toml"))]:
    proj = tomllib.loads(p.read_text())["project"]
    if proj["name"] == "deepctl-plugin-example":
        continue
    for d in proj.get("dependencies", []):
        name = re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0]
        if name.startswith("deepctl"):
            continue
        reqs.append(d)
        m = re.search(r">=\s*([^,;\s]+)", d)
        floors.append(f"{proj['name']}\t{name.lower()}\t{m.group(1) if m else ''}")
Path(sys.argv[1]).write_text(f"deepctl=={version}\n" + "\n".join(sorted(set(reqs))) + "\n")
Path(sys.argv[2]).write_text("\n".join(floors) + "\n")
PYEOF
  uv venv -q -p "$PY" "$ENV/venv" || exit 1
  if ! uv pip install -q --python "$ENV/venv/bin/python" --resolution lowest-direct --no-build \
      --find-links "$DIST" -r "$ENV/requirements.txt"; then
    echo "FAIL lowest-direct: the lockstep closure does not resolve at its floors"; exit 1
  fi
  uv pip freeze --python "$ENV/venv/bin/python" > "$ENV/freeze.txt"
  # Test tools, constrained so they can't move anything already resolved.
  grep -v "^deepctl" "$ENV/freeze.txt" > "$ENV/constraints.txt"
  if ! uv pip install -q --python "$ENV/venv/bin/python" -c "$ENV/constraints.txt" $TEST_TOOLS; then
    echo "FAIL lowest-direct: the test tools can't install next to the floors"; exit 1
  fi
  echo "== resolved at the floors"; grep -v "^deepctl" "$ENV/freeze.txt" | tr '\n' ' '; echo
  echo "== declared floors below what the closure resolves (never tested; can be raised)"
  "$ENV/venv/bin/python" - "$ENV/floors.tsv" "$ENV/freeze.txt" <<'PYEOF'
import re, sys
from packaging.version import Version
got = {}
for line in open(sys.argv[2]):
    if "==" in line:
        n, v = line.strip().split("==", 1)
        got[re.sub(r"[-_.]+", "-", n).lower()] = v
low = {}
for line in open(sys.argv[1]):
    pkg, name, floor = line.rstrip("\n").split("\t")
    n = re.sub(r"[-_.]+", "-", name)
    if floor and n in got and Version(floor) < Version(got[n]):
        low.setdefault(f"{name}>={floor} (resolves {got[n]})", []).append(pkg)
for k, pkgs in sorted(low.items()):
    print(f"  {k}: {len(pkgs)} package(s)")
print(f"  {len(low)} distinct floor(s)")
PYEOF
fi

if [ "$PHASE" != install ]; then
  fail=0
  PROBE_OUT="$ENV/probe.jsonl" "$ENV/venv/bin/python" -I "$HERE/probe.py" lowest-direct \
    "$ENV/venv/bin/python" "$ENV/venv/bin/dg" "uv pip check --python $ENV/venv/bin/python" \
    --min-commands 23 || true
  grep -q '"ok": true' "$ENV/probe.jsonl" || fail=1
  rm -f "$ENV/probe.jsonl"
  # The unit tests import the installed packages, not the source tree. Each
  # test file and package suite runs in its own process: some suites register
  # commands on the shared CLI object, which an editable install hides but an
  # installed one doesn't.
  for t in tests/unit/test_*.py $(ls -d packages/*/tests | grep -v deepctl-plugin-example); do
    out=$(cd "$ENV" && "$ENV/venv/bin/python" -m pytest -q -p no:cacheprovider --rootdir "$OLDPWD" \
        -c "$OLDPWD/pyproject.toml" "$OLDPWD/$t" 2>&1)
    rc=$?; [ $rc = 5 ] && rc=0   # pytest 5: the suite has no tests
    echo "$([ $rc = 0 ] && echo PASS || echo FAIL) $t: $(echo "$out" | grep -E '[0-9]+ (passed|failed|error)' | tail -1)"
    if [ $rc != 0 ]; then fail=1; echo "$out" | grep -E '^(FAILED|ERROR)' | head -20; fi
  done
  echo "lowest-direct: $([ $fail = 0 ] && echo passed || echo FAILED)"
  exit $fail
fi
