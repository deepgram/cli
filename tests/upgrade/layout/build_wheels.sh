#!/bin/sh
# Runs INSIDE python:3.12 (network: PyPI only, for build backends). Mounts:
#   /src (ro) a clean `git archive` of the tree under test   /s (ro) scripts   /w work dir (rw)
# Builds the lockstep layout at 0.5.0 and 0.6.0 and the planned 1.0 layout at
# 1.0.0rc1 and 1.0.0, one wheel dir per version: /w/wheels-<V>.
set -eu
pip -q install --disable-pip-version-check "setuptools>=61" wheel hatchling build packaging >/dev/null
for spec in "lockstep 0.5.0" "lockstep 0.6.0" "relocated 1.0.0rc1" "relocated 1.0.0"; do
  set -- $spec; MODE=$1; V=$2
  rm -rf /w/tree-$V /w/wheels-$V; mkdir -p /w/wheels-$V
  python -I /s/mk_layouts.py /src /w/tree-$V $MODE $V > /w/projs-$V.txt
  while read -r P; do
    pip -q wheel --no-deps --no-build-isolation --disable-pip-version-check -w /w/wheels-$V "$P" >/dev/null 2>/w/build-err.txt || { echo "BUILD FAIL $P"; cat /w/build-err.txt; exit 1; }
  done < /w/projs-$V.txt
  rm -f /w/wheels-$V/deepctl_plugin_example-*
  echo "$V: $(ls /w/wheels-$V | wc -l) wheels"
done
