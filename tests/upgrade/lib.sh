# Sourced inside the upgrade-matrix image. Every cell runs with --network none.
# Mounts: /w (ro) work dir, /s (ro) these scripts, /o (rw) this group's output.
#
# /w layout (run.sh builds it):
#   wheelhouse/          third-party wheels plus published first-party wheels
#   pins/env-<V>.txt     the full closure a user resolved for a published V
#   field/<V>/           first-party wheels for a field version not on PyPI
#                        (optional; e.g. a release candidate built locally)
#   lockstep/cur/        this tree's 30 wheels at version $CUR
#   lockstep/next/       the same tree at $NEXT (the following release)
set -u
export DEEPCTL_TELEMETRY_DISABLED=1 DEEPCTL_TELEMETRY_DSN=http://k@127.0.0.1:9/1 CI=1
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
mkdir -p /idx/pool
W=/w; WH=$W/wheelhouse
CUR=$(cat $W/lockstep/cur/VERSION); NEXT=$(cat $W/lockstep/next/VERSION)
FIELD=$(cat $W/FIELD_VERSIONS)                # e.g. "0.3.3 0.4.0 0.4.1"
LCUR="$W/lockstep/cur"; LNEXT="$W/lockstep/next"
field_dirs(){ for d in $W/field/*/; do [ -d "$d" ] && printf '%s ' "${d%/}"; done; }
mk(){ python -I /s/mkidx.py /idx/$1 /idx/pool "${@:2}" 2>>/o/idx.log; }
serve(){ python -I /s/serve.py /idx >/dev/null 2>&1 & sleep 1; }
setidx(){ ln -sfn /idx/$1 /idx/cur; echo "  [index -> $1]"; }
export PIP_INDEX_URL=http://127.0.0.1:8000/cur/simple/ UV_INDEX_URL=http://127.0.0.1:8000/cur/simple/
export UV_DEFAULT_INDEX=http://127.0.0.1:8000/cur/simple/ UV_PYTHON_DOWNLOADS=never UV_NO_PROGRESS=1
pipvenv(){ rm -rf $1; python -m venv $1 && $1/bin/pip -q install pip==25.3; }
# Install a published version's real closure (pins/env-V.txt) into a pip venv.
old_pip(){ pipvenv $2; $2/bin/pip -q install -r /w/pins/env-$1.txt; }
probe(){ python -I /s/probe.py "$@"; }
# x CMD...: run an install step, keep the last lines of its output in the log
x(){ echo "  + $*"; "$@" > /tmp/x.out 2>&1; local rc=$?; grep -v "^\s*$" /tmp/x.out | grep -iv "notice\]" | tail -4 | sed 's/^/    | /'; echo "    rc=$rc"; return $rc; }
pp(){ probe "$1" "$2/bin/python" "$2/bin/dg" "$2/bin/pip check" "${@:3}"; }                     # pip venv
up(){ probe "$1" "$3/bin/python" "$3/bin/dg" "$2 pip check --python $3/bin/python" "${@:4}"; }  # uv venv: up ID UV DIR
# Dist names (normalized) of the lockstep set.
lockstep_dists(){ ls $LCUR/*.whl | xargs -n1 basename | sed -E 's/-[0-9].*//; s/_/-/g' | grep -v '^deepctl-plugin-example$' | sort; }
# A cell that is not a probe: verdict line in the same PASS/FAIL format.
verdict(){ local ok=$1 cell=$2; shift 2; if [ "$ok" = 1 ]; then echo "PASS $cell: $*"; else echo "FAIL $cell: $*"; fi
  printf '{"cell": "%s", "ok": %s, "note": "%s"}\n' "$cell" "$([ "$ok" = 1 ] && echo true || echo false)" "$*" >> /o/results.jsonl; }
