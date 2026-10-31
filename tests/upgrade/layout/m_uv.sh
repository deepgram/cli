#!/bin/bash
# Group U: uv pip / uv tool / uvx. Arg 1 = uv binary (uv = latest 0.13.6, uv08 = 0.8.24).
source /s/lib.sh; build_states
UV=$1; T=$UV
export UV_CACHE_DIR=/tmp/uvcache-$T
echo "== $T: $($UV --version)"
echo "-- uv pip chain"
setidx I040; old_uv $UV 0.4.0 /u
setidx I050; x env VIRTUAL_ENV=/u $UV pip install -U deepctl; up $T-uvpip-chain-0.5.0 $UV /u --expect-version 0.5.0
setidx I050rc; x env VIRTUAL_ENV=/u $UV pip install -U deepctl; up $T-uvpip-chain-noPre-stays $UV /u --expect-version 0.5.0
x env VIRTUAL_ENV=/u $UV pip install --prerelease allow -U deepctl; up $T-uvpip-chain-pre-rc1 $UV /u --expect-version 1.0.0rc1
setidx I100; x env VIRTUAL_ENV=/u $UV pip install -U deepctl; up $T-uvpip-chain-1.0.0 $UV /u --expect-version 1.0.0
x env VIRTUAL_ENV=/u $UV pip uninstall deepctl-cmd-listen; up $T-uvpip-chain-uninstall-listen $UV /u --expect-version 1.0.0
echo "-- uv pip direct 0.4.0 -> 1.0.0"
for R in deepctl deepctl-core deepctl-cmd-listen; do
  setidx I040; old_uv $UV 0.4.0 /u; setidx I100; x env VIRTUAL_ENV=/u $UV pip install -U $R
  up $T-uvpip-040-U-$R $UV /u --expect-version 1.0.0
done
setidx I040; old_uv $UV 0.3.3 /u; setidx I100; x env VIRTUAL_ENV=/u $UV pip install -U deepctl; up $T-uvpip-033-U-deepctl $UV /u --expect-version 1.0.0
echo "-- uv tool"
export UV_TOOL_DIR=/tmp/ut UV_TOOL_BIN_DIR=/tmp/ub
tp(){ probe "$1" /tmp/ut/deepctl/bin/python /tmp/ub/dg "$UV pip check --python /tmp/ut/deepctl/bin/python" "${@:2}"; }
rm -rf /tmp/ut /tmp/ub; setidx I040; x $UV tool install deepctl; tp $T-tool-install-unpinned-040 --expect-version 0.4.0
setidx I100; x $UV tool upgrade deepctl; tp $T-tool-upgrade-unpinned-to-1.0.0 --expect-version 1.0.0
x $UV pip uninstall --python /tmp/ut/deepctl/bin/python deepctl-cmd-listen; tp $T-tool-then-uninstall-listen --expect-version 1.0.0
rm -rf /tmp/ut /tmp/ub; setidx I040; x $UV tool install deepctl==0.4.0; setidx I100
x $UV tool upgrade deepctl; tp $T-tool-pinned-then-upgrade --expect-version 0.4.0 --note "pinned receipt: upgrade respects pin (expected)"
x $UV tool install deepctl@latest; tp $T-tool-pinned-then-install-latest --expect-version 1.0.0
rm -rf /tmp/ut /tmp/ub; setidx I040; x $UV tool install deepctl
setidx I050; x $UV tool upgrade deepctl; tp $T-tool-chain-0.5.0 --expect-version 0.5.0
setidx I050rc; x $UV tool upgrade deepctl; tp $T-tool-chain-noPre-stays --expect-version 0.5.0
x $UV tool upgrade --prerelease allow deepctl; tp $T-tool-chain-pre-rc1 --expect-version 1.0.0rc1
setidx I100; x $UV tool upgrade deepctl; tp $T-tool-chain-1.0.0 --expect-version 1.0.0
for WIN in W100noroot Y100pair; do
  rm -rf /tmp/ut /tmp/ub; setidx I050; x $UV tool install deepctl; setidx $WIN; x $UV tool upgrade deepctl; tp $T-tool-upgrade-during-$WIN --expect-version 0.5.0
  rm -rf /tmp/ut /tmp/ub; setidx $WIN; x $UV tool install deepctl; tp $T-tool-fresh-install-during-$WIN --expect-version 0.5.0
done
echo "-- uvx"; rm -rf /tmp/ut /tmp/ub; export UV_CACHE_DIR=/tmp/uvcache-x-$T  # uvx prefers an installed tool, so remove it first
xp(){ local SPEC=$1 ID=$2; shift 2; local PYX; PYX=$($UV tool run --from "$SPEC" python -c 'import sys;print(sys.executable)' 2>/dev/null | tail -1)
  probe "$ID" "$PYX" "$UV tool run --from $SPEC dg" "$UV pip check --python $PYX" "$@"; }
setidx I040; xp deepctl $T-uvx-at-040 --expect-version 0.4.0
setidx I100; xp deepctl $T-uvx-cached-after-1.0-publish --note "uvx reuses cached env? expect 0.4.0 until cache refresh"
xp deepctl@latest $T-uvx-latest-after-1.0-publish --expect-version 1.0.0
export UV_CACHE_DIR=/tmp/uvcache2-$T; setidx I100; xp deepctl $T-uvx-cold-cache-1.0 --expect-version 1.0.0
setidx W100noroot; export UV_CACHE_DIR=/tmp/uvcache3-$T; xp deepctl $T-uvx-cold-during-W100noroot --expect-version 0.5.0
setidx Y100pair; export UV_CACHE_DIR=/tmp/uvcache4-$T; xp deepctl $T-uvx-cold-during-Y100pair --expect-version 0.5.0
