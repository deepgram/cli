#!/bin/bash
# Lockstep cells: fresh installs at CUR, -U of every dist from CUR to NEXT,
# upgrades from field versions to CUR, and the dependency-prerelease guard.
# Usage: lockstep.sh fresh|bump-pip|bump-uv|field|prerelease
source /s/lib.sh
N=$(lockstep_dists | wc -l)
FD=$(field_dirs)
mk FIELD $WH $FD                       # what users have before CUR ships
mk CUR   $WH $FD $LCUR                 # CUR published
mk NEXT  $WH $FD $LCUR $LNEXT          # NEXT published
for V in $FIELD; do                    # each field version as "latest" (newer roots hidden)
  hide=""; for U in $FIELD $CUR; do [ "$(printf '%s\n%s\n' "$V" "$U" | sort -V | tail -1)" != "$V" ] && hide="$hide,deepctl-$U"; done
  mk AT-$V $WH:-${hide#,} $([ -n "$FD" ] && for d in $FD; do printf '%s:-%s ' $d "${hide#,}"; done)
done
serve
LS="--expect-dists $N --expect-all --min-commands 23"
case "$1" in
fresh)  # a fresh install of CUR resolves exactly the lockstep set, with every tool
  setidx CUR
  pipvenv /f; x /f/bin/pip install deepctl==$CUR; pp L-fresh-pip-install /f --expect-version $CUR $LS
  rm -rf /u; uv venv -q /u; x env VIRTUAL_ENV=/u uv pip install deepctl==$CUR; up L-fresh-uv-pip-install uv /u --expect-version $CUR $LS
  export UV_TOOL_DIR=/tmp/ut UV_TOOL_BIN_DIR=/tmp/ub; rm -rf /tmp/ut /tmp/ub
  x uv tool install deepctl==$CUR; probe L-fresh-uv-tool-install /tmp/ut/deepctl/bin/python /tmp/ub/dg "uv pip check --python /tmp/ut/deepctl/bin/python" --expect-version $CUR $LS
  for BK in uv pip; do
    export PIPX_HOME=/tmp/px-$BK PIPX_BIN_DIR=/tmp/pb-$BK PIPX_DEFAULT_BACKEND=$BK
    x pipx install deepctl==$CUR; probe L-fresh-pipx-$BK-install /tmp/px-$BK/venvs/deepctl/bin/python /tmp/pb-$BK/dg "pipx runpip deepctl check" --expect-version $CUR $LS
  done
  unset PIPX_DEFAULT_BACKEND
  ;;
bump-pip|bump-uv)  # -U of any one dist from a CUR install moves the whole set to NEXT
  setidx CUR; pipvenv /base; /base/bin/pip -q install deepctl==$CUR
  rm -rf /ubase; uv venv -q /ubase; VIRTUAL_ENV=/ubase uv pip install -q deepctl==$CUR
  clone(){ rm -rf $2; cp -a $1 $2; sed -i "s#$1#$2#g" $2/bin/* $2/pyvenv.cfg 2>/dev/null; }
  setidx NEXT
  for R in $(lockstep_dists); do
    if [ "$1" = bump-pip ]; then
      clone /base /n; x /n/bin/pip install -U $R; pp L-bump-pip-U-$R /n --expect-version $NEXT $LS
    else
      clone /ubase /n; x env VIRTUAL_ENV=/n uv pip install -U $R; up L-bump-uv-pip-U-$R uv /n --expect-version $NEXT $LS
    fi
  done
  ;;
field)  # upgrades from every field version to CUR, with pip, uv tool and pipx
  export UV_TOOL_DIR=/tmp/ut UV_TOOL_BIN_DIR=/tmp/ub PIPX_HOME=/tmp/px PIPX_BIN_DIR=/tmp/pb
  for V in $FIELD; do
    setidx AT-$V
    if [ -f /w/pins/env-$V.txt ]; then old_pip $V /p; else pipvenv /p; /p/bin/pip -q install deepctl==$V; fi
    pp L-field-$V-start-pip /p --expect-version $V --min-commands 20
    setidx CUR; x /p/bin/pip install -U deepctl; pp L-field-$V-pip-U-deepctl /p --expect-version $CUR $LS
    rm -rf /tmp/ut /tmp/ub; setidx AT-$V; x uv tool install deepctl
    setidx CUR; x uv tool upgrade deepctl; probe L-field-$V-uv-tool-upgrade /tmp/ut/deepctl/bin/python /tmp/ub/dg "uv pip check --python /tmp/ut/deepctl/bin/python" --expect-version $CUR $LS
    rm -rf /tmp/px /tmp/pb; setidx AT-$V; x pipx install deepctl
    setidx CUR; x pipx upgrade deepctl; probe L-field-$V-pipx-upgrade /tmp/px/venvs/deepctl/bin/python /tmp/pb/dg "pipx runpip deepctl check" --expect-version $CUR $LS
  done
  ;;
prerelease)  # a third-party major prerelease can't get in; the uncapped control can
  setidx CUR
  BETA=$(ls $WH | grep -E '^sentry_sdk-3\.0\.0(a|b|rc)[0-9]+-' | head -1 | cut -d- -f2)
  [ -n "$BETA" ] || { verdict 0 L-pre-fixture "no sentry-sdk 3.0 prerelease wheel in the wheelhouse"; exit 0; }
  pipvenv /q; x /q/bin/pip install deepctl==$CUR sentry-sdk==$BETA; rc=$?
  verdict $([ $rc -ne 0 ] && ! /q/bin/pip show -q deepctl >/dev/null 2>&1 && echo 1 || echo 0) L-pre-pip-forced-$BETA "pip install deepctl==$CUR sentry-sdk==$BETA must not resolve (rc=$rc)"
  rm -rf /u; uv venv -q /u; x env VIRTUAL_ENV=/u uv pip install deepctl==$CUR sentry-sdk==$BETA; rc=$?
  verdict $([ $rc -ne 0 ] && echo 1 || echo 0) L-pre-uv-forced-$BETA "uv pip install deepctl==$CUR sentry-sdk==$BETA must not resolve (rc=$rc)"
  pipvenv /q; /q/bin/pip -q install deepctl==$CUR; x /q/bin/pip install --pre -U deepctl
  S=$(/q/bin/pip show sentry-sdk 2>/dev/null | sed -n 's/^Version: //p')
  verdict $(case "$S" in 2.*) echo 1;; *) echo 0;; esac) L-pre-pip-pre-U "pip --pre -U deepctl keeps sentry-sdk on 2.x (got $S)"
  pp L-pre-pip-pre-U-loads /q --expect-version $CUR $LS
  OLD=$(echo $FIELD | tr ' ' '\n' | grep -x 0.4.0 || true)
  if [ -n "$OLD" ]; then  # control: the uncapped 0.4.0 resolves with the beta (and breaks)
    setidx AT-0.4.0; pipvenv /c; x /c/bin/pip install deepctl==0.4.0 sentry-sdk==$BETA; rc=$?
    verdict $([ $rc -eq 0 ] && echo 1 || echo 0) L-pre-control-0.4.0-uncapped "the uncapped 0.4.0 resolves with sentry-sdk $BETA (rc=$rc)"
    /c/bin/dg --help >/tmp/c.out 2>&1; echo "  control dg --help rc=$? $(grep -m1 -o "has no attribute '[a-z_]*'" /tmp/c.out)"
  fi
  ;;
*) echo "usage: lockstep.sh fresh|bump-pip|bump-uv|field|prerelease"; exit 2 ;;
esac
