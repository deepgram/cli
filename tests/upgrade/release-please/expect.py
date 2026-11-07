"""Assert the release PRs a dry run proposed: exactly the given releases.

Usage: python3 expect.py OUT_JSON COMPONENT=VERSION [...]   (VERSION * = any)
"""

import json
import sys

prs = json.load(open(sys.argv[1]))
want = dict(a.split("=", 1) for a in sys.argv[2:])
# The root component has include-component-in-tag false, so release-please
# reports it with an empty component name.
got = {(r["component"] or "deepctl"): r["version"] for pr in prs for r in pr["releases"]}
ok = len(prs) == 1 and set(got) == set(want) and all(
    v == "*" or got[k] == v for k, v in want.items()
)
print(("PASS" if ok else "FAIL") + f" one release PR proposing {want}: got {len(prs)} PR(s) {got}")
sys.exit(0 if ok else 1)
