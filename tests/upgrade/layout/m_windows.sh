#!/bin/bash
# Group W: partial-upload windows (deps first, root last) and the yank pair. pip 25.3 + uv latest.
source /s/lib.sh; build_states
start(){ # start STATE_LABEL DIR : 0.4.0 or 0.5.0 pip venv
  if [ $1 = 040 ]; then setidx I040; old_pip 0.4.0 $2; else setidx I050; pipvenv $2; $2/bin/pip -q install deepctl==0.5.0; fi; }
ustart(){ if [ $1 = 040 ]; then setidx I040; old_uv uv 0.4.0 $2; else setidx I050; rm -rf $2; uv venv -q $2; VIRTUAL_ENV=$2 uv pip install -q deepctl==0.5.0; fi; }
for WIN in W050noroot W050coreonly; do
  for R in deepctl deepctl-core deepctl-cmd-listen; do
    start 040 /w1; setidx $WIN; x /w1/bin/pip install -U $R; pp W-$WIN-from040-pip-U-$R /w1 --note "window: 0.5.0 deps visible, root 0.5.0 not"
  done
done
for WIN in Wrcnoroot; do
  for R in deepctl deepctl-core deepctl-cmd-listen; do
    start 050 /w1; setidx $WIN; x /w1/bin/pip install --pre -U $R; pp W-$WIN-from050-pip-U-pre-$R /w1 --expect-version 0.5.0
  done
  pipvenv /w1; x /w1/bin/pip install --pre deepctl-core; pp W-$WIN-fresh-pip-pre-install-core /w1 --note "fresh SDK install during rc window"
done
for WIN in W100noroot W100half Wrootonly; do
  for FROM in 040 050; do
    EXP=$([ $FROM = 040 ] && echo 0.4.0 || echo 0.5.0)
    for R in deepctl deepctl-core deepctl-cmd-listen; do
      start $FROM /w1; setidx $WIN; x /w1/bin/pip install -U $R; pp W-$WIN-from$FROM-pip-U-$R /w1 --expect-version $EXP
      ustart $FROM /u1; setidx $WIN; x env VIRTUAL_ENV=/u1 uv pip install -U $R; up W-$WIN-from$FROM-uvpip-U-$R uv /u1 --expect-version $EXP
    done
  done
  setidx $WIN; pipvenv /w1; x /w1/bin/pip install deepctl-core; pp W-$WIN-fresh-pip-install-core /w1 --expect-version 0.5.0
  pipvenv /w1; x /w1/bin/pip install deepctl; pp W-$WIN-fresh-pip-install-deepctl /w1 --expect-version 0.5.0
done
echo "== yank"
for Y in Y100pair Y100root; do
  for FROM in 040 050; do
    EXP=$([ $FROM = 040 ] && echo 0.4.0 || echo 0.5.0)
    for R in deepctl deepctl-core deepctl-cmd-listen; do
      start $FROM /w1; setidx $Y; x /w1/bin/pip install -U $R
      [ $R = deepctl ] && E=0.5.0 || E=$EXP
      pp Y-$Y-from$FROM-pip-U-$R /w1 --note "$Y; plain -U deepctl moves 0.4.0->0.5.0 (latest non-yanked final)"
      ustart $FROM /u1; setidx $Y; x env VIRTUAL_ENV=/u1 uv pip install -U $R; up Y-$Y-from$FROM-uvpip-U-$R uv /u1 --note "$Y"
    done
  done
  setidx $Y; pipvenv /w1; x /w1/bin/pip install deepctl-core; pp Y-$Y-fresh-pip-install-core /w1 --note "$Y"
  pipvenv /w1; x /w1/bin/pip install deepctl; pp Y-$Y-fresh-pip-install-deepctl /w1 --note "$Y"
done
