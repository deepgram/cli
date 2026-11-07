#!/bin/sh
# Offline release-please dry run over a lockstep tree. Runs inside a Node
# image with git and python3 (node:24), with no network.
#
# Mounts: /src (ro) the tree to test, /rp (ro) a directory whose node_modules
# holds release-please at the version the pinned action bundles, /o (rw) out.
# Scenarios, each from the same baseline tagged v<V> and
# deepctl-plugin-example-v<example version>:
#   A  feat: touching one package      -> one release (root), V+1 everywhere
#   B  fix: touching the plugin example -> no root release, example only
#   C  Release-As: X.Y.0 footer         -> X.Y.0 everywhere
set -eu
H=$(cd "$(dirname "$0")" && pwd)
export NODE_PATH=/rp/node_modules HOME=/tmp GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid \
  GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
V=$(python3 -c 'import json;print(json.load(open("/src/.github/.release-please-manifest.json"))["."])')
EX=$(python3 -c 'import json;print(json.load(open("/src/.github/.release-please-manifest.json"))["packages/deepctl-plugin-example"])')
baseline() {
  rm -rf /tmp/r && cp -a /src /tmp/r && cd /tmp/r && rm -rf .git
  git init -q -b main && git add -A && git commit -qm "chore: baseline"
  git tag "v$V" && git tag "deepctl-plugin-example-v$EX"
  SHA=$(git rev-parse HEAD)
  export RELEASES="[{\"tagName\":\"v$V\",\"sha\":\"$SHA\"},{\"tagName\":\"deepctl-plugin-example-v$EX\",\"sha\":\"$SHA\"}]"
}
scenario() {  # ID FILE MESSAGE
  baseline
  echo "# $1" >> "$2"
  git commit -qam "$3"
  echo "== scenario $1: $(git log -1 --format=%s) (touches $2)"
  node "$H/dry_run.js" /tmp/r "/o/release-please-$1.json"
  git add -N . 2>/dev/null || true
  git diff --stat > "/o/release-please-$1.diffstat"
  git diff > "/o/release-please-$1.diff"
}
scenario A packages/deepctl-cmd-listen/src/deepctl_cmd_listen/__init__.py "feat(listen): stream interim results"
NEXT=$(python3 -c "v='$V'.split('.');print(f'{v[0]}.{v[1]}.{int(v[2])+1}')")
python3 "$H/expect.py" /o/release-please-A.json "deepctl=$NEXT"
python3 "$H/assert_bump.py" /tmp/r "$V" "$NEXT"
scenario B packages/deepctl-plugin-example/src/deepctl_plugin_example/__init__.py "fix(example): clarify the sample output"
python3 "$H/expect.py" /o/release-please-B.json "deepctl-plugin-example=*"
scenario C packages/deepctl-core/src/deepctl_core/__init__.py "feat(core): lockstep versions

Release-As: 0.5.0"
python3 "$H/expect.py" /o/release-please-C.json "deepctl=0.5.0"
python3 "$H/assert_bump.py" /tmp/r "$V" 0.5.0
echo "release-please dry run: all scenarios passed"
