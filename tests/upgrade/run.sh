#!/bin/bash
# Upgrade matrix for the lockstep release layout.
#
#   tests/upgrade/run.sh [lockstep|release-please|layout|all]     (default: lockstep)
#
# lockstep        Builds this tree's 30 wheels at the root version (CUR) and at
#                 the next patch (NEXT), then, against a local PEP 503 index:
#                 fresh installs of CUR with pip, uv pip, uv tool and pipx; -U of
#                 every dist from a CUR install with NEXT published (pip and uv);
#                 upgrades from each field version to CUR; and a third-party
#                 major prerelease (sentry-sdk 3.0 beta) that must not resolve.
# release-please  Runs the release-please version the release workflow uses over
#                 this tree, offline, and checks one version and one tag.
# layout          The planned 1.0 layout (relocated code, tombstones) against
#                 the field versions: tests/upgrade/layout/.
#
# Network is used only to build wheels and fetch third-party wheels and tools.
# Every container that installs or runs deepctl has --network none, telemetry
# disabled and a dead telemetry endpoint. At most 4 cell containers run at once.
#
# Environment:
#   WORK            work dir (default: ${TMPDIR:-/tmp}/deepctl-upgrade)
#   FIELD_VERSIONS  published versions to upgrade from (default: "0.3.3 0.4.0")
#   FIELD_DIR       optional dir of <version>/ wheel dirs for field versions
#                   that are not on PyPI (their versions go in FIELD_VERSIONS too)
#   SKIP_BUILD=1, SKIP_FETCH=1   reuse what is already in WORK
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-${TMPDIR:-/tmp}/deepctl-upgrade}; mkdir -p "$WORK"; WORK=$(cd "$WORK" && pwd)
FIELD_VERSIONS=${FIELD_VERSIONS:-"0.3.3 0.4.0"}
IMG=deepctl-upgrade-matrix:py3.12
TEL=(-e DEEPCTL_TELEMETRY_DISABLED=1 -e DEEPCTL_TELEMETRY_DSN=http://k@127.0.0.1:9/1)
OFF=(--rm --network none --memory 1500m "${TEL[@]}")
NET=(--rm --memory 1500m "${TEL[@]}")
MODE=${1:-lockstep}

source_tree(){ rm -rf "$WORK/src"; mkdir -p "$WORK/src"; git -C "$REPO" archive HEAD | tar -xf - -C "$WORK/src"; }

build_lockstep(){
  source_tree; rm -rf "$WORK/lockstep"; mkdir -p "$WORK/lockstep/cur" "$WORK/lockstep/next"
  docker run "${NET[@]}" -v "$WORK/src:/src:ro" -v "$WORK/lockstep:/out" python:3.12-slim sh -ec '
    pip -q install --disable-pip-version-check --root-user-action=ignore uv==0.13.6 packaging
    cp -a /src /t && cd /t
    uv build -q --all-packages --wheel --out-dir /out/cur
    python -c "import tomllib;print(tomllib.load(open(\"pyproject.toml\",\"rb\"))[\"project\"][\"version\"])" > /out/cur/VERSION
    NEXT=$(python -c "v=open(\"/out/cur/VERSION\").read().strip().split(\".\");print(\".\".join(v[:2]+[str(int(v[2])+1)]))")
    sed -i "0,/^version = \"[^\"]*\"/s//version = \"$NEXT\"/" pyproject.toml
    python scripts/check_dependency_floors.py --fix >/dev/null
    uv build -q --all-packages --wheel --out-dir /out/next
    echo "$NEXT" > /out/next/VERSION
    rm -f /out/*/deepctl_plugin_example-*'
}

fetch_lockstep(){
  mkdir -p "$WORK/wheelhouse" "$WORK/pins" "$WORK/field"
  [ -n "${FIELD_DIR:-}" ] && cp -R "$FIELD_DIR"/. "$WORK/field/"
  echo "$FIELD_VERSIONS" > "$WORK/FIELD_VERSIONS"
  docker run "${NET[@]}" -e FIELD_VERSIONS="$FIELD_VERSIONS" -v "$WORK:/w" python:3.12-slim sh -ec '
    export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
    pip -q install uv==0.13.6
    FL="--find-links /w/lockstep/cur --find-links /w/lockstep/next"
    for d in /w/field/*/; do [ -d "$d" ] && FL="$FL --find-links $d"; done
    for V in $FIELD_VERSIONS; do
      [ -d /w/field/$V ] && { pip download -q --only-binary :all: -d /w/wheelhouse $FL "deepctl==$V"; continue; }
      echo "deepctl==$V" | uv pip compile -q --python-version 3.12 - -o /w/pins/env-$V.txt
      pip wheel -q --no-deps -r /w/pins/env-$V.txt -w /w/wheelhouse
      pip download -q --only-binary :all: -d /w/wheelhouse "deepctl==$V"
    done
    for V in $(cat /w/lockstep/cur/VERSION) $(cat /w/lockstep/next/VERSION); do
      pip download -q --only-binary :all: -d /w/wheelhouse $FL "deepctl==$V"; done
    pip download -q --no-deps --only-binary :all: -d /w/wheelhouse "sentry-sdk==3.0.0b2" "pip==25.3" "pip==26.2.2"
    cd /w/wheelhouse && for f in /w/lockstep/*/*.whl /w/field/*/*.whl; do [ -e "$f" ] && rm -f "$(basename "$f")"; done; true'
}

run_lockstep(){
  local groups=("fresh prerelease" "bump-pip" "bump-uv" "field")
  for g in "${groups[@]}"; do
    (
      for G in $g; do
        mkdir -p "$WORK/out/$G"; rm -f "$WORK/out/$G/results.jsonl"
        docker run "${OFF[@]}" -v "$WORK:/w:ro" -v "$HERE:/s:ro" -v "$WORK/out/$G:/o" "$IMG" \
          bash /s/cells/lockstep.sh "$G" > "$WORK/out/$G/run.log" 2>&1
        echo "$G: $(grep -c '^PASS' "$WORK/out/$G/run.log") pass, $(grep -c '^FAIL' "$WORK/out/$G/run.log") fail"
      done
    ) &
  done
  wait
  ! grep -h '^FAIL' "$WORK"/out/{fresh,prerelease,bump-pip,bump-uv,field}/run.log
}

run_release_please(){
  mkdir -p "$WORK/rp" "$WORK/out/release-please"
  [ -d "$WORK/rp/node_modules/release-please" ] || docker run --rm --memory 1500m -v "$WORK/rp:/rp" -w /rp node:24-slim \
    sh -c 'npm init -y >/dev/null && npm install -q --no-audit --no-fund release-please@17.6.0 >/dev/null'
  source_tree
  docker run --rm --network none --memory 1500m -v "$WORK/src:/src:ro" -v "$WORK/rp:/rp:ro" \
    -v "$HERE/release-please:/h:ro" -v "$WORK/out/release-please:/o" node:24 sh /h/run.sh
}

run_layout(){
  source_tree
  [ "${SKIP_BUILD:-}" = 1 ] || docker run "${NET[@]}" -v "$WORK/src:/src:ro" -v "$HERE/layout:/s:ro" -v "$WORK:/w" python:3.12 sh /s/build_wheels.sh
  [ "${SKIP_FETCH:-}" = 1 ] || docker run "${NET[@]}" -v "$WORK:/w" -v "$WORK/out:/o" -v "$HERE/layout:/s:ro" python:3.12-slim sh /s/fetch.sh
  docker run "${OFF[@]}" -v "$WORK:/w:ro" -v "$HERE/layout:/s:ro" "$IMG" \
    python -I /s/static_checks.py /w/wheelhouse /w/wheels-0.5.0 /w/wheels-1.0.0rc1 /w/wheels-1.0.0 | tee "$WORK/out/static_checks.txt"
  local groups=("pip siblings" "windows uv-latest" "uv-0.8 pipx" "next backedge rc")
  script_for(){ case $1 in uv-latest) echo "m_uv.sh uv";; uv-0.8) echo "m_uv.sh uv08";; *) echo "m_$1.sh";; esac; }
  for g in "${groups[@]}"; do
    ( for G in $g; do mkdir -p "$WORK/out/$G"; rm -f "$WORK/out/$G/results.jsonl"
        docker run "${OFF[@]}" -v "$WORK:/w:ro" -v "$HERE/layout:/s:ro" -v "$HERE/probe.py:/s/probe.py:ro" \
          -v "$HERE/mkidx.py:/s/mkidx.py:ro" -v "$HERE/serve.py:/s/serve.py:ro" -v "$WORK/out/$G:/o" "$IMG" \
          bash /s/$(script_for "$G") > "$WORK/out/$G/run.log" 2>&1
        echo "$G: $(grep -c '^PASS' "$WORK/out/$G/run.log") pass, $(grep -c '^FAIL' "$WORK/out/$G/run.log") fail"; done ) &
  done
  wait
  python3 "$HERE/layout/aggregate.py" "$WORK/out"
}

docker build -q -t "$IMG" "$HERE/image" >/dev/null
case "$MODE" in
  lockstep) [ "${SKIP_BUILD:-}" = 1 ] || build_lockstep; [ "${SKIP_FETCH:-}" = 1 ] || fetch_lockstep; run_lockstep ;;
  release-please) run_release_please ;;
  layout) run_layout ;;
  all) run_release_please && { [ "${SKIP_BUILD:-}" = 1 ] || build_lockstep; [ "${SKIP_FETCH:-}" = 1 ] || fetch_lockstep; run_lockstep; } && run_layout ;;
  *) sed -n 2,26p "$0"; exit 2 ;;
esac
