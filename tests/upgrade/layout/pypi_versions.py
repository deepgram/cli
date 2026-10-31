"""Print release history (version, first upload time, yanked) for deepctl dists from PyPI JSON."""
import json, sys, urllib.request
for name in sys.argv[1:]:
    d = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json"))
    rows = []
    for v, files in d["releases"].items():
        if files:
            rows.append((min(f["upload_time_iso_8601"] for f in files), v, any(f["yanked"] for f in files)))
    rows.sort()
    print(name, " ".join(f"{v}@{t[:16]}{'(Y)' if y else ''}" for t, v, y in rows[-12:]))
