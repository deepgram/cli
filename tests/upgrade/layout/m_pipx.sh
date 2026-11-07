#!/bin/bash
# Group X: pipx 1.18.11 (+ plugin cells with pip).
source /s/lib.sh; build_states
BK=${PIPX_DEFAULT_BACKEND:-auto}; echo "pipx backend: $BK"
export PIPX_HOME=/tmp/px PIPX_BIN_DIR=/tmp/pb PIPX_SHARED_LIBS=/tmp/pxs
xp(){ probe "$1-$BK" /tmp/px/venvs/deepctl/bin/python /tmp/pb/dg "pipx runpip deepctl check" "${@:2}"; }
rm -rf /tmp/px /tmp/pb; setidx I040; x pipx install deepctl; xp X-pipx-install-040 --expect-version 0.4.0
setidx I050; x pipx upgrade deepctl; xp X-pipx-upgrade-0.5.0 --expect-version 0.5.0
setidx I050rc; x pipx upgrade deepctl; xp X-pipx-upgrade-noPre-stays --expect-version 0.5.0
x pipx upgrade --pip-args=--pre deepctl; xp X-pipx-upgrade-pre-rc1 --expect-version 1.0.0rc1
setidx I100; x pipx upgrade deepctl; xp X-pipx-upgrade-1.0.0 --expect-version 1.0.0
x pipx runpip deepctl uninstall -y deepctl-cmd-listen; xp X-pipx-then-uninstall-listen --expect-version 1.0.0
rm -rf /tmp/px /tmp/pb; setidx I040; x pipx install deepctl; setidx I100; x pipx upgrade deepctl; xp X-pipx-040-direct-1.0.0 --expect-version 1.0.0
x pipx upgrade-all; xp X-pipx-upgrade-all-idempotent --expect-version 1.0.0
rm -rf /tmp/px /tmp/pb; setidx I050; x pipx install deepctl; setidx W100noroot; x pipx upgrade deepctl; xp X-pipx-upgrade-during-W100noroot --expect-version 0.5.0
rm -rf /tmp/px /tmp/pb; setidx I050; x pipx install deepctl; setidx Y100pair; x pipx upgrade deepctl; xp X-pipx-upgrade-during-Y100pair --expect-version 0.5.0
echo "== plugin (third-party deepctl-plugin-example 0.1.12, deepctl-core>=0.1.10)"
setidx I040; old_pip 0.4.0 /p; x /p/bin/pip install deepctl-plugin-example; pp P-040-with-plugin /p --expect-version 0.4.0 --need example
setidx I100; x /p/bin/pip install -U deepctl; pp P-plugin-survives-U-to-1.0.0 /p --expect-version 1.0.0 --need example
pipvenv /p; x /p/bin/pip install deepctl-plugin-example; pp P-fresh-plugin-install-pulls-cli-1.0.0 /p --expect-version 1.0.0 --need example
rm -rf /tmp/ut /tmp/ub; export UV_TOOL_DIR=/tmp/ut UV_TOOL_BIN_DIR=/tmp/ub; setidx I040; x uv tool install deepctl --with deepctl-plugin-example
setidx I100; x uv tool upgrade deepctl; probe P-uvtool-with-plugin-upgrade-1.0.0 /tmp/ut/deepctl/bin/python /tmp/ub/dg "uv pip check --python /tmp/ut/deepctl/bin/python" --expect-version 1.0.0 --need example
