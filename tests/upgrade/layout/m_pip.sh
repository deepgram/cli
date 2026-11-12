#!/bin/bash
# Group A: pip 25.3 channels from the real 0.4.0 / 0.3.3 closures.
source /s/lib.sh; build_states
echo "== A1 chain 0.4.0 -> 0.5.0 -> 1.0.0rc1 (--pre) -> 1.0.0, then uninstall tombstones"
setidx I040; old_pip 0.4.0 /a1; pp A1.0-start /a1 --expect-version 0.4.0
setidx I050; x /a1/bin/pip install -U deepctl; pp A1.1-pip-U-0.5.0 /a1 --expect-version 0.5.0
setidx I050rc; x /a1/bin/pip install -U deepctl; pp A1.2-pip-U-noPre-stays-0.5.0 /a1 --expect-version 0.5.0
x /a1/bin/pip install --pre -U deepctl; pp A1.3-pip-U-pre-rc1 /a1 --expect-version 1.0.0rc1
setidx I100; x /a1/bin/pip install -U deepctl; pp A1.4-pip-U-1.0.0 /a1 --expect-version 1.0.0
x /a1/bin/pip uninstall -y deepctl-cmd-listen; pp A1.5-uninstall-tomb-listen /a1 --expect-version 1.0.0 --note "pip check expected to name the missing tombstone"
x /a1/bin/pip uninstall -y deepctl-shared-utils deepctl-telemetry deepctl-cmd-mcp; pp A1.6-uninstall-3-more-tombs /a1 --expect-version 1.0.0
echo "== A2 0.4.0 -> 1.0.0 directly"
for R in deepctl deepctl-core deepctl-cmd-listen; do
  setidx I040; old_pip 0.4.0 /a2; setidx I100; x /a2/bin/pip install -U $R
  pp A2-040-pip-U-$R /a2 --expect-version 1.0.0
  x /a2/bin/pip uninstall -y deepctl-cmd-listen; pp A2-040-pip-U-$R-then-uninstall-listen /a2 --expect-version 1.0.0
done
echo "== A3 0.3.3 -> 1.0.0 directly"
for R in deepctl deepctl-core deepctl-cmd-listen deepctl-cmd-debug-toolkit; do
  setidx I040; old_pip 0.3.3 /a3; setidx I100; x /a3/bin/pip install -U $R
  pp A3-033-pip-U-$R /a3 --expect-version 1.0.0
done
echo "== A4 0.3.3 -> 0.5.0 (lockstep) via -U of root, core, a sibling"
for R in deepctl deepctl-core deepctl-cmd-listen; do
  setidx I040; old_pip 0.3.3 /a4; setidx I050; x /a4/bin/pip install -U $R
  pp A4-033-pip-U-$R-at-0.5.0 /a4 --note "expect 0.5.0 for root; sibling/core -U may leave root at 0.3.3"
done
echo "== A5 fresh installs at I100"; setidx I100
pipvenv /a5; x /a5/bin/pip install deepctl; pp A5-fresh-pip-install-deepctl /a5 --expect-version 1.0.0
pipvenv /a5; x /a5/bin/pip install deepctl-core; pp A5-fresh-pip-install-deepctl-core /a5 --expect-version 1.0.0
pipvenv /a5; x /a5/bin/pip install deepctl==0.4.0; pp A5-fresh-pin-0.4.0-at-I100 /a5 --expect-version 0.4.0 --note "old pin must still resolve; siblings drift to latest 0.x?"
