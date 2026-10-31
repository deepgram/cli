#!/bin/bash
# Group N: lockstep after lockstep. From a real 0.5.0 env: `-U <each dist>` to 0.6.0 (lockstep == pins
# on both sides), and `-U <each dist>` straight to 1.0.0 (tombstones force the root up).
source /s/lib.sh; build_states; build_states_next
DISTS=$(ls $P10 | sed -E 's/-[0-9].*//; s/_/-/g' | sort)
setidx I050; pipvenv /base; /base/bin/pip -q install deepctl==0.5.0
clone(){ rm -rf /n; cp -a /base /n; sed -i "s#/base#/n#g" /n/bin/* /n/pyvenv.cfg 2>/dev/null; }
for STATE in I060 I100b; do
  EXP=$([ $STATE = I060 ] && echo 0.6.0 || echo 1.0.0)
  for R in $DISTS; do
    clone; setidx $STATE; x /n/bin/pip install -U $R; pp N-$STATE-from050-pip-U-$R /n --expect-version $EXP
  done
done
for STATE in I060 I100b; do
  EXP=$([ $STATE = I060 ] && echo 0.6.0 || echo 1.0.0)
  for R in deepctl deepctl-core deepctl-cmd-listen deepctl-shared-utils; do
    setidx I050; rm -rf /u; uv venv -q /u; VIRTUAL_ENV=/u uv pip install -q deepctl==0.5.0
    setidx $STATE; x env VIRTUAL_ENV=/u uv pip install -U $R; up N-$STATE-from050-uvpip-U-$R uv /u --expect-version $EXP
  done
done
