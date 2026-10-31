#!/bin/bash
# Group R: how an rc tester should install 1.0.0rc1 without pulling third-party prereleases.
source /s/lib.sh; build_states
for UV in uv uv08; do
  export UV_CACHE_DIR=/tmp/c-$UV
  setidx I050; rm -rf /u; $UV venv -q /u; VIRTUAL_ENV=/u $UV pip install -q deepctl==0.5.0; setidx I050rc
  x env VIRTUAL_ENV=/u $UV pip install deepctl==1.0.0rc1; up R-$UV-pip-install-pin-rc1 $UV /u --expect-version 1.0.0rc1
  setidx I100; x env VIRTUAL_ENV=/u $UV pip install -U deepctl; up R-$UV-pin-rc1-then-U-1.0.0 $UV /u --expect-version 1.0.0
  setidx I050; rm -rf /u; $UV venv -q /u; VIRTUAL_ENV=/u $UV pip install -q deepctl==0.5.0; setidx I050rc
  x env VIRTUAL_ENV=/u $UV pip install --prerelease explicit -U deepctl; up R-$UV-prerelease-explicit-U $UV /u --note "does explicit mode reach rc1 without a pin?"
  x env VIRTUAL_ENV=/u $UV pip install -U --prerelease allow --upgrade-package deepctl deepctl; up R-$UV-prerelease-allow-upgrade-package-only $UV /u --note "-P deepctl instead of -U all"
  export UV_TOOL_DIR=/tmp/ut UV_TOOL_BIN_DIR=/tmp/ub; rm -rf /tmp/ut /tmp/ub
  setidx I050; $UV tool install -q deepctl; setidx I050rc
  x $UV tool upgrade --prerelease allow deepctl; probe R-$UV-tool-upgrade-prerelease-allow /tmp/ut/deepctl/bin/python /tmp/ub/dg "$UV pip check --python /tmp/ut/deepctl/bin/python" --expect-version 1.0.0rc1
  rm -rf /tmp/ut /tmp/ub; x $UV tool install deepctl==1.0.0rc1; probe R-$UV-tool-install-pin-rc1 /tmp/ut/deepctl/bin/python /tmp/ub/dg "$UV pip check --python /tmp/ut/deepctl/bin/python" --expect-version 1.0.0rc1
  setidx I100; x $UV tool install deepctl@latest; probe R-$UV-tool-pin-rc1-then-latest /tmp/ut/deepctl/bin/python /tmp/ub/dg "$UV pip check --python /tmp/ut/deepctl/bin/python" --expect-version 1.0.0
done
setidx I050rc; pipvenv /p; x /p/bin/pip install --pre deepctl; pp R-pip-fresh-install-pre /p --expect-version 1.0.0rc1 --note "--pre is global for pip: fresh resolve may pick sentry-sdk beta"
pipvenv /p; x /p/bin/pip install deepctl==1.0.0rc1; pp R-pip-fresh-install-pin-rc1 /p --expect-version 1.0.0rc1
echo "-- uv08 workaround: --prerelease allow on an existing env without -U"
setidx I050; rm -rf /u; uv08 venv -q /u; VIRTUAL_ENV=/u uv08 pip install -q deepctl==0.5.0; setidx I050rc
x env VIRTUAL_ENV=/u uv08 pip install --prerelease allow deepctl==1.0.0rc1; up R-uv08-prerelease-allow-pin-rc1-noU uv08 /u --expect-version 1.0.0rc1
rm -rf /tmp/ut /tmp/ub; x uv08 tool install --prerelease allow deepctl==1.0.0rc1; probe R-uv08-tool-install-prerelease-allow-pin-rc1 /tmp/ut/deepctl/bin/python /tmp/ub/dg "uv08 pip check --python /tmp/ut/deepctl/bin/python" --expect-version 1.0.0rc1
echo "-- released 0.4.0 against sentry-sdk 3.0.0b2 (an uncapped release)"
setidx I050rc; old_pip 0.4.0 /q; x /q/bin/pip install sentry-sdk==3.0.0b2; pp R-released-0.4.0-with-sentry-sdk-3.0.0b2 /q --expect-version 0.4.0
echo "-- uv pip check vs missing dependency (after tombstone uninstall)"
setidx I100; rm -rf /u; uv venv -q /u; VIRTUAL_ENV=/u uv pip install -q deepctl; VIRTUAL_ENV=/u uv pip uninstall -q deepctl-cmd-listen
x uv pip check --python /u/bin/python; x uv08 pip check --python /u/bin/python
