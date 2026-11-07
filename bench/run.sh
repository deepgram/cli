#!/bin/sh
# Run the benchmark. Examples:
#   bench/run.sh                      # dg at HEAD plus every pinned competitor
#   bench/run.sh --subjects dg        # dg only
#   bench/run.sh --ref 793fa60 --subjects dg,voice
# Prints the path of results.json; evaluate it with bench/check.py.
set -eu
exec python3 "$(dirname "$0")/bench.py" run "$@"
