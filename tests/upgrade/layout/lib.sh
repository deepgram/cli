# Sourced inside the upgrade-matrix image. Mounts: /w (ro) wheels, /s (ro) scripts, /o (rw) out.
set -u
export DEEPCTL_TELEMETRY_DISABLED=1 DEEPCTL_TELEMETRY_DSN=http://k@127.0.0.1:9/1 CI=1
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
mkdir -p /idx/pool
W=/w; WH=$W/wheelhouse; P05=$W/wheels-0.5.0; PRC=$W/wheels-1.0.0rc1; P10=$W/wheels-1.0.0; PEX="$W/wheels-plugin $W/wheels-pipxshared"
mk(){ python -I /s/mkidx.py /idx/$1 /idx/pool "${@:2}" 2>>/o/idx.log; }
# Index states (what PyPI shows at a point in the release)
build_states(){
  mk I040   $WH $PEX
  mk I050   $WH $PEX $P05
  mk I050rc $WH $PEX $P05 $PRC
  mk I100   $WH $PEX $P05 $PRC $P10
  # partial-upload windows (upload order: every non-root dist first, deepctl last)
  mk W050noroot  $WH $PEX $P05:-deepctl-0.5.0
  mk W050coreonly $WH $PEX $P05:+deepctl-core-0.5.0
  mk Wrcnoroot   $WH $PEX $P05 $PRC:-deepctl-1.0.0rc1
  mk W100noroot  $WH $PEX $P05 $PRC $P10:-deepctl-1.0.0
  mk W100half    $WH $PEX $P05 $PRC $P10:+deepctl-core-1.0.0,deepctl-cmd-a,deepctl-cmd-b,deepctl-cmd-c,deepctl-cmd-debug,deepctl-cmd-f,deepctl-cmd-i,deepctl-cmd-k,deepctl-cmd-l
  mk Wrootonly   $WH $PEX $P05 $PRC $P10:+deepctl-1.0.0
  # yank: root and core 1.0.0 yanked together, no 1.0.1
  mk Y100pair  --yank deepctl-1.0.0 --yank deepctl-core-1.0.0 $WH $PEX $P05 $PRC $P10
  mk Y100root  --yank deepctl-1.0.0 $WH $PEX $P05 $PRC $P10
  python -I /s/serve.py /idx >/dev/null 2>&1 &
  sleep 1
}
setidx(){ ln -sfn /idx/$1 /idx/cur; echo "  [index -> $1]"; }
export PIP_INDEX_URL=http://127.0.0.1:8000/cur/simple/ UV_INDEX_URL=http://127.0.0.1:8000/cur/simple/
export UV_DEFAULT_INDEX=http://127.0.0.1:8000/cur/simple/ UV_PYTHON_DOWNLOADS=never UV_NO_PROGRESS=1
pipvenv(){ rm -rf $1; python -m venv $1 && $1/bin/pip -q install pip==25.3; }
old_pip(){ pipvenv $2; $2/bin/pip -q install -r /w/pins/env-$1.txt; }
old_uv(){ rm -rf $3; $1 venv -q $3 && VIRTUAL_ENV=$3 $1 pip install -q -r /w/pins/env-$2.txt; }
probe(){ python -I /s/probe.py "$@"; }
# x CMD...: run an install step, keep the last lines of its output in the log
x(){ echo "  + $*"; "$@" > /tmp/x.out 2>&1; local rc=$?; grep -v "^\s*$" /tmp/x.out | grep -iv "notice\]" | tail -4 | sed 's/^/    | /'; echo "    rc=$rc"; return 0; }
pp(){ probe "$1" "$2/bin/python" "$2/bin/dg" "$2/bin/pip check" "${@:3}"; }          # pip venv
up(){ probe "$1" "$3/bin/python" "$3/bin/dg" "$2 pip check --python $3/bin/python" "${@:4}"; }  # uv venv: up ID UV DIR
P06=$W/wheels-0.6.0
build_states_next(){
  mk I060  $WH $PEX $P05 $P06
  mk I100b $WH $PEX $P05 $P06 $PRC $P10
}
