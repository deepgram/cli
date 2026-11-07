"""Every package that imports websockets declares the same floor.

13.1 is the oldest release the redirect matrix covers for each socket
dg opens (listen, Flux speak, the debug probe and this server).
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[3]


def test_websockets_floor_matches_across_packages():
    floors = {}
    for toml in sorted(PACKAGES.glob("*/pyproject.toml")):
        found = re.findall(r'"websockets([^"]*)"', toml.read_text(encoding="utf-8"))
        if found:
            floors[toml.parent.name] = found
    assert "deepctl-cmd-debug-browser" in floors
    assert {spec for specs in floors.values() for spec in specs} == {">=13.1,<18"}, floors
