#!/bin/bash
# Group U-uvx re-run: uvx cells alone, longer per-command timeout (first run hit harness timeouts under load).
source /s/lib.sh; build_states; UV=$1; T=$UV; export PROBE_TIMEOUT=300 PROBE_WORKERS=3
echo "-- uvx"; rm -rf /tmp/ut /tmp/ub; export UV_CACHE_DIR=/tmp/uvcache-x-$T  # uvx prefers an installed tool, so remove it first
xp(){ local SPEC=$1 ID=$2; shift 2; local PYX; PYX=$($UV tool run --from "$SPEC" python -c 'import sys;print(sys.executable)' 2>/dev/null | tail -1)
  probe "$ID" "$PYX" "$UV tool run --from $SPEC dg" "$UV pip check --python $PYX" "$@"; }
setidx I040; xp deepctl $T-uvx-at-040 --expect-version 0.4.0
setidx I100; xp deepctl $T-uvx-cached-after-1.0-publish --note "uvx reuses cached env? expect 0.4.0 until cache refresh"
xp deepctl@latest $T-uvx-latest-after-1.0-publish --expect-version 1.0.0
export UV_CACHE_DIR=/tmp/uvcache2-$T; setidx I100; xp deepctl $T-uvx-cold-cache-1.0 --expect-version 1.0.0
setidx W100noroot; export UV_CACHE_DIR=/tmp/uvcache3-$T; xp deepctl $T-uvx-cold-during-W100noroot --expect-version 0.5.0
setidx Y100pair; export UV_CACHE_DIR=/tmp/uvcache4-$T; xp deepctl $T-uvx-cold-during-Y100pair --expect-version 0.5.0
