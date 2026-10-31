#!/bin/sh
# Runs INSIDE python:3.12-slim with network to PyPI only. Downloads the REAL
# 0.3.3 / 0.4.0 closures (as resolved on their release day, via --exclude-newer),
# the third-party closure of the locally built 0.5.0, 0.6.0 and 1.0 wheels, and the tools.
# Mounts: /w (rw) work dir, /o (rw) evidence out dir.
set -eu
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
pip -q install uv==0.13.6 >/dev/null
mkdir -p /w/wheelhouse /w/pins
# real released closures, resolved as a user would have on release day
uv pip compile -q --python-version 3.12 --exclude-newer 2026-10-12T12:00:00Z - -o /w/pins/env-0.3.3.txt <<R
deepctl==0.3.3
R
uv pip compile -q --python-version 3.12 --exclude-newer 2026-10-20T12:00:00Z - -o /w/pins/env-0.4.0.txt <<R
deepctl==0.4.0
R
cp /w/pins/env-*.txt /o/
for f in /w/pins/env-0.3.3.txt /w/pins/env-0.4.0.txt; do pip -q wheel --no-deps -r $f -w /w/wheelhouse; done
# also the latest-of-each-sibling 0.4.0-era (what `pip install deepctl==0.4.0` gives today)
pip -q wheel deepctl==0.4.0 -w /w/wheelhouse
# third-party closure of local wheels (first-party resolved from local dirs only)
for V in 0.5.0 0.6.0 1.0.0rc1 1.0.0; do
  pip -q wheel --pre --find-links /w/wheels-$V "deepctl==$V" "deepctl-core==$V" -w /w/wheelhouse
done
# tools
mkdir -p /w/tools
pip -q download --only-binary :all: -d /w/tools "pip>=25,<26" pipx "uv" "uv==0.8.24"
pip -q wheel -w /w/wheelhouse "pip>=25,<26" setuptools wheel
uv --version > /o/tools_fetched.txt; ls /w/tools >> /o/tools_fetched.txt
# yank fidelity needs nothing extra; drop local first-party copies pip wheel may have written
cd /w/wheelhouse && for f in deepctl-*0.5.0-* deepctl-*0.6.0-* deepctl-*1.0.0*; do [ -e "$f" ] && rm -f "$f"; done; true
ls /w/wheelhouse | wc -l
# the third-party plugin fixture as published, and the pip that pipx's pip
# backend needs for its shared libs (it refuses pip<26.2.2)
mkdir -p /w/wheels-plugin /w/wheels-pipxshared
pip -q download --no-deps -d /w/wheels-plugin "deepctl-plugin-example==0.1.12"
pip -q download --no-deps -d /w/wheels-pipxshared "pip==26.2.2"
