# Baselines

One `<runner-label>.json` per runner, written by the first scheduled run on that
runner (`python3 bench/bench.py baseline <results.json>`) and refreshed by PR.
`check.py --baseline` is optional; with no file for a runner it compares nothing.
