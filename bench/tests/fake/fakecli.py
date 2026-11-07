"""Fake `dg` for the harness self-test. Installed at the same path as the real
CLI, with build-time knobs baked into fake_config.json next to it:
  delay_ms  extra latency before answering --version and the MCP initialize
  egress    when true, every invocation attempts an outbound connect()
  fail      when true, every invocation exits 1 immediately
  imports   a stdlib module to import on every invocation (a forbidden-list probe)
  nopyc     when true, import a module from a read-only dir with no .pyc
  nopyc_writable  when true, import a module with no .pyc from a writable dir
            (compiled once, then cached: only the first run in a container sees it)
It answers --version, --help, `transcribe --help` and a stdio MCP handshake
with 10 annotated tools, using the stdlib only.
"""

import json
import os
import sys
import time

CFG = json.load(
    open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fake_config.json"))
)


def egress():
    if CFG.get("egress"):
        import socket

        s = socket.socket()
        s.settimeout(0.05)
        try:
            s.connect(("10.255.255.1", 443))
        except OSError:
            pass


def delay():
    if CFG.get("delay_ms"):
        time.sleep(CFG["delay_ms"] / 1000.0)


TOOLS = [
    {
        "name": "tool_%d" % i,
        "description": "Fake tool %d." % i,
        "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}},
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    }
    for i in range(10)
]


def mcp():
    out = sys.stdout
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        if m["method"] == "initialize":
            delay()
            res = {
                "protocolVersion": m["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": CFG["version"]},
            }
        elif m["method"] == "tools/list":
            res = {"tools": TOOLS}
        else:
            out.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": m["id"],
                        "error": {"code": -32601, "message": "nope"},
                    }
                )
                + "\n"
            )
            out.flush()
            continue
        out.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}) + "\n")
        out.flush()


def main():
    if CFG.get("fail"):
        return 1
    if CFG.get("imports"):
        __import__(CFG["imports"])
    if CFG.get("nopyc"):
        sys.path.insert(0, "/opt/fakemods")
        __import__("fake_nopyc")
    if CFG.get("nopyc_writable"):
        sys.path.insert(0, os.path.expanduser("~/fakemods"))
        __import__("fake_nopyc_w")
    egress()
    a = sys.argv[1:]
    if a == ["--version"]:
        delay()
        print("dg, version " + CFG["version"])
    elif a == ["--help"]:
        print("Usage: dg [OPTIONS] COMMAND")
    elif a == ["transcribe", "--help"]:
        print("Usage: dg transcribe [OPTIONS] FILE")
    elif a[:1] == ["mcp"]:
        if a[1:]:
            print(json.dumps({"error": "unsupported"}))
            return 2
        mcp()
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
