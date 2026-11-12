#!/bin/bash
# Group S: `pip install -U <each sibling>` from the real 0.4.0 closure, at 0.5.0 (lockstep) and at 1.0.0.
source /s/lib.sh; build_states
SIBS=$(ls $P10 | sed -E 's/-[0-9].*//; s/_/-/g' | grep -v '^deepctl$' | sort)
setidx I040; old_pip 0.4.0 /base
for STATE in I050 I100; do
  for R in $SIBS; do
    rm -rf /s1; cp -a /base /s1; sed -i "s#/base#/s1#g" /s1/bin/* /s1/pyvenv.cfg 2>/dev/null
    setidx $STATE; x /s1/bin/pip install -U $R
    if [ $STATE = I100 ]; then pp S-$STATE-pip-U-$R /s1 --expect-version 1.0.0; else pp S-$STATE-pip-U-$R /s1 --note "0.4.0 root floors are >=, lockstep only binds 0.5.0 dists"; fi
  done
done
