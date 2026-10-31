#!/bin/bash
# Group B: the `deepctl==V` back-edge every lockstep package declares. From a
# 0.5.0 install, -U of each dist with 0.6.0 published moves the whole set; also
# 0.4.0 -> 0.5.0 via -U of a package, and 0.5.0 -> 1.0.0.
source /s/lib.sh; build_states; build_states_next
DISTS=$(ls $P10 | sed -E 's/-[0-9].*//; s/_/-/g' | sort)
setidx I050; pipvenv /base; /base/bin/pip -q install deepctl==0.5.0
clone(){ rm -rf /n; cp -a /base /n; sed -i "s#/base#/n#g" /n/bin/* /n/pyvenv.cfg 2>/dev/null; }
for R in $DISTS; do clone; setidx I060; x /n/bin/pip install -U $R; pp B-I060-from050-pip-U-$R /n --expect-version 0.6.0; done
for R in deepctl-core deepctl-cmd-listen deepctl-shared-utils; do
  setidx I050; rm -rf /u; uv venv -q /u; VIRTUAL_ENV=/u uv pip install -q deepctl==0.5.0
  setidx I060; x env VIRTUAL_ENV=/u uv pip install -U $R; up B-I060-from050-uvpip-U-$R uv /u --expect-version 0.6.0
  setidx I040; old_pip 0.4.0 /o4; setidx I050; x /o4/bin/pip install -U $R; pp B-I050-from040-pip-U-$R /o4 --expect-version 0.5.0
  setidx I050; clone; setidx I100; x /n/bin/pip install -U $R; pp B-I100-from050-pip-U-$R /n --expect-version 1.0.0
done
