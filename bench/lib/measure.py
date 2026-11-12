"""In-container measurement driver for the bench harness.

Runs inside a bench image (started with --network none) and writes one JSON
file per row into OUT_DIR (default /out). Stdlib only; runs on Python 3.9+
(the imports rows need 3.10+ for sys.stdlib_module_names; they run only in dg images),
because competitor images bring their own distro python3.

Every subcommand takes the measured command after `--`.

  hf       hyperfine -N timing (warm rows, floors, data path)
  mcp      MCP stdio probe: spawn -> initialize, -> tools/list; tool stats; junk bytes
  egress   zero-egress check: strace connect() count, hang-proxy accepts, timing backstop
  imports  modules imported on a path (PYTHONPROFILEIMPORTTIME) vs allowlist/forbidden lists
  verbose  source compiles under PYTHONVERBOSE (the no-recompile check)
  rss      peak RSS via GNU time
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time

OUT = os.environ.get("OUT_DIR", "/out")
HERE = os.path.dirname(os.path.abspath(__file__))
GATES = os.environ.get("BENCH_GATES", os.path.join(os.path.dirname(HERE), "gates.json"))
PROXY_PORT = 8899
FAKE_PORT = 8080

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "dg-bench", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def mcp_session_bytes() -> bytes:
    return b"".join((json.dumps(m) + "\n").encode() for m in (INIT, INITIALIZED, LIST))


# ----------------------------------------------------------------- helpers
def median(xs):
    return statistics.median(xs) if xs else None


def mad(xs):
    if not xs:
        return None
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def stats_ms(times_ms):
    return {
        "median": round(median(times_ms), 4),
        "mad": round(mad(times_ms), 4),
        "mean": round(statistics.fmean(times_ms), 4),
        "stddev": round(statistics.pstdev(times_ms), 4) if len(times_ms) > 1 else None,
        "min": round(min(times_ms), 4),
        "max": round(max(times_ms), 4),
        "n": len(times_ms),
    }


def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    obj = dict(obj, row=name)
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    summary = {
        k: obj.get(k) for k in ("value", "unit", "error") if obj.get(k) is not None
    }
    print(name, json.dumps(summary))


def env_with(pairs):
    env = dict(os.environ)
    for p in pairs or []:
        k, _, v = p.partition("=")
        env[k] = v
    return env


def session_file():
    fd, path = tempfile.mkstemp(prefix="mcp-session-", suffix=".jsonl")
    os.write(fd, mcp_session_bytes())
    os.close(fd)
    return path


def wav_header(total):
    """44-byte PCM WAV header (16 kHz, mono, 16-bit) for a file of `total` bytes."""
    data = max(0, total - 44)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", data)
    )


def make_files(specs):
    """--make-file /tmp/x.wav:64K creates a file of that size: silence, with a WAV header for *.wav."""
    mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
    for spec in specs or []:
        path, _, size = spec.rpartition(":")
        n = int(size[:-1]) * mult[size[-1]] if size[-1] in mult else int(size)
        if os.path.exists(path) and os.path.getsize(path) == n:
            continue
        with open(path, "wb") as f:
            chunk = b"\0" * (1 << 20)
            left = n
            if path.endswith(".wav"):
                f.write(wav_header(n))
                left -= 44
            while left > 0:
                f.write(chunk[: min(left, len(chunk))])
                left -= len(chunk)


class FakeServer:
    """Starts lib/fake_dg.py on 127.0.0.1:FAKE_PORT for data-path rows."""

    def __init__(self, words):
        self.words = words
        self.p = None

    def __enter__(self):
        env = dict(os.environ, WORDS=str(self.words), FAKE_PORT=str(FAKE_PORT))
        self.p = subprocess.Popen(
            [sys.executable, "-I", os.path.join(HERE, "fake_dg.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        end = time.time() + 15
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", FAKE_PORT), timeout=0.2).close()
                return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("fake server did not start")

    def __exit__(self, *a):
        self.p.kill()
        self.p.wait()


class HangProxy(threading.Thread):
    """Accepts connections and never answers: a blackholed egress path."""

    def __init__(self):
        super().__init__(daemon=True)
        self.s = socket.socket()
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", PROXY_PORT))
        self.s.listen(512)
        self.accepts = 0
        self.held = []

    def run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            self.accepts += 1
            self.held.append(c)


def hyperfine(cmd, runs, warmup, env=None, input_path=None, extra=None):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    args = [
        "hyperfine",
        "-N",
        "-i",
        "--style",
        "none",
        "--warmup",
        str(warmup),
        "--runs",
        str(runs),
        "--export-json",
        path,
    ]
    if input_path:
        args += ["--input", input_path]
    args += extra or []
    args += ["--", " ".join(_shq(c) for c in cmd)]
    r = subprocess.run(
        args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    if r.returncode != 0:
        raise RuntimeError("hyperfine failed: " + r.stderr[-500:])
    with open(path) as f:
        res = json.load(f)["results"][0]
    os.unlink(path)
    times = [t * 1000 for t in res["times"]]
    return times, res.get("exit_codes", [])


def _shq(s):
    # hyperfine -N splits the command with shell-words rules
    if re.fullmatch(r"[A-Za-z0-9_./=:@%+,-]+", s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def bad_codes(codes, ok):
    """Exit codes outside the row's accepted set (default {0}); a failed run is never a valid timing."""
    return sorted(set(codes) - set(ok))


# ----------------------------------------------------------------- rows
def cmd_hf(a):
    make_files(a.make_file)
    env = env_with(a.env)
    inp = session_file() if a.mcp_session else None

    def go():
        times, codes = hyperfine(a.cmd, a.runs, a.warmup, env=env, input_path=inp)
        row = {
            "unit": "ms",
            "method": "hyperfine -N --warmup %d --runs %d" % (a.warmup, a.runs),
        }
        row.update(stats_ms(times))
        row["value"] = row["median"]
        row["exit_codes"] = sorted(set(codes))
        row["times"] = [round(t, 4) for t in times]
        bad = bad_codes(codes, a.ok_codes)
        if bad:
            row["value"], row["error"] = None, "exit codes %s" % bad
        if a.capture_bytes:
            r = subprocess.run(
                a.cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            row["stdout_bytes"] = len(r.stdout)
            try:
                json.loads(r.stdout)
                row["stdout_is_json"] = True
            except ValueError:
                row["stdout_is_json"] = False
            if a.require_json and not (
                row["stdout_is_json"] and row["stdout_bytes"] > 0
            ):
                row["value"] = None
                row.setdefault("error", "stdout is not a non-empty JSON document")
        return row

    try:
        if a.serve is not None:
            with FakeServer(a.serve):
                row = go()
        else:
            row = go()
    except Exception as e:  # noqa: BLE001 - recorded, not raised
        row = {"value": None, "unit": "ms", "error": str(e)[:300]}
    row["cmd"] = a.cmd
    write(a.name, row)


def cmd_rss(a):
    make_files(a.make_file)
    env = env_with(a.env)
    kbs, walls, codes = [], [], []

    def go():
        for _ in range(a.runs):
            fd, tf = tempfile.mkstemp()
            os.close(fd)
            t = time.perf_counter()
            r = subprocess.run(
                ["/usr/bin/time", "-f", "%M", "-o", tf] + a.cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            walls.append((time.perf_counter() - t) * 1000)
            codes.append(r.returncode)
            with open(tf) as f:
                kbs.append(int(f.read().strip().splitlines()[-1]))
            os.unlink(tf)

    if a.serve is not None:
        with FakeServer(a.serve):
            go()
    else:
        go()
    mbs = [k / 1024 for k in kbs]
    row = {
        "unit": "MB",
        "method": "GNU time %%M, %d runs" % a.runs,
        "value": round(median(mbs), 1),
        "samples_mb": [round(m, 1) for m in mbs],
        "wall_ms": [round(w) for w in walls],
        "exit_codes": sorted(set(codes)),
        "cmd": a.cmd,
    }
    bad = bad_codes(codes, a.ok_codes)
    if bad:
        row["value"], row["error"] = None, "exit codes %s" % bad
    write(a.name, row)


def _mcp_once(cmd, env, timeout, detail=False):
    """One MCP session. Returns timings (ms) and, with detail, tool stats."""
    t0 = time.perf_counter()
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ, "out")
    sel.register(p.stderr, selectors.EVENT_READ, "err")
    buf = {"out": b"", "err": b""}
    junk = []
    msgs = {}
    open_streams = 2

    def pump(want_id, deadline):
        nonlocal open_streams
        while time.perf_counter() < deadline and open_streams:
            for key, _ in sel.select(timeout=max(0.0, deadline - time.perf_counter())):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_streams -= 1
                    continue
                if key.data == "err":
                    buf["err"] = (buf["err"] + chunk)[-4000:]
                    continue
                buf["out"] += chunk
                while b"\n" in buf["out"]:
                    line, buf["out"] = buf["out"].split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        m = json.loads(line)
                        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
                            raise ValueError
                    except ValueError:
                        junk.append(line)
                        continue
                    if "id" in m:
                        msgs[m["id"]] = (time.perf_counter(), m, len(line))
                if want_id in msgs:
                    return msgs[want_id]
        return msgs.get(want_id)

    out = {}
    try:
        p.stdin.write((json.dumps(INIT) + "\n").encode())
        p.stdin.flush()
        r = pump(1, t0 + timeout)
        if not r or "result" not in r[1]:
            out["error"] = (
                "no initialize result"
                if not r
                else "initialize error: %s" % json.dumps(r[1].get("error"))[:200]
            )
        else:
            out["init_ms"] = (r[0] - t0) * 1000
            if detail:
                res = r[1]["result"]
                out["protocol"] = res.get("protocolVersion")
                out["server"] = (res.get("serverInfo") or {}).get("name")
                out["server_version"] = (res.get("serverInfo") or {}).get("version")
            p.stdin.write(
                (json.dumps(INITIALIZED) + "\n" + json.dumps(LIST) + "\n").encode()
            )
            p.stdin.flush()
            r2 = pump(2, time.perf_counter() + timeout)
            if r2 and "result" in r2[1]:
                out["list_ms"] = (r2[0] - t0) * 1000
                if detail:
                    tools = r2[1]["result"].get("tools", [])
                    n = len(tools)
                    size = r2[
                        2
                    ]  # the raw tools/list response line as received, without its newline
                    out.update(
                        tool_count=n,
                        annotations=sum(1 for t in tools if t.get("annotations")),
                        output_schema=sum(1 for t in tools if t.get("outputSchema")),
                        tools_list_bytes=size,
                        bytes_per_tool=round(size / n, 1) if n else None,
                    )
            elif detail:
                out["list_error"] = (
                    "no tools/list result"
                    if not r2
                    else json.dumps(r2[1].get("error"))[:200]
                )
        if detail:
            # offline cleanliness: still alive after the handshake, no junk on stdout
            pump(-1, time.perf_counter() + 0.3)
            out["alive_after_list"] = p.poll() is None
    except (BrokenPipeError, OSError) as e:
        out.setdefault("error", "pipe: %s" % e)
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        if detail:
            pump(-1, time.perf_counter() + 0.2)
            out["exit_code"] = p.returncode
            out["stdout_junk_lines"] = len(junk)
            out["stdout_junk_bytes"] = sum(len(j) + 1 for j in junk) + len(
                buf["out"].strip()
            )
            out["exit_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def cmd_mcp(a):
    env = env_with(a.env)
    first = _mcp_once(a.cmd, env, a.timeout, detail=True)
    detail = {k: v for k, v in first.items() if k not in ("init_ms", "list_ms")}
    base = {
        "cmd": a.cmd,
        "method": "spawn -> response, perf_counter; %d warmup + %d runs; sizes are the raw tools/list response bytes (KB = 1024)"
        % (a.warmup, a.runs),
        "protocol_sent": INIT["params"]["protocolVersion"],
    }
    if "init_ms" not in first:
        write(
            a.name + "_init",
            dict(base, value=None, unit="ms", error=first.get("error"), detail=detail),
        )
        write(
            a.name + "_list",
            dict(base, value=None, unit="ms", error=first.get("error")),
        )
    else:
        for _ in range(a.warmup):
            _mcp_once(a.cmd, env, a.timeout)
        inits, lists = [], []
        for _ in range(a.runs):
            r = _mcp_once(a.cmd, env, a.timeout)
            if "init_ms" in r:
                inits.append(r["init_ms"])
            if "list_ms" in r:
                lists.append(r["list_ms"])
        row = dict(base, unit="ms", detail=detail, **stats_ms(inits))
        row["value"] = row["median"]
        row["times"] = [round(t, 4) for t in inits]
        write(a.name + "_init", row)
        if lists:
            row = dict(base, unit="ms", **stats_ms(lists))
            row["value"] = row["median"]
            row["times"] = [round(t, 4) for t in lists]
        else:
            row = dict(
                base,
                value=None,
                unit="ms",
                error=detail.get("list_error", "no tools/list result"),
            )
        write(a.name + "_list", row)
    # derived single-value rows used by gates
    d = detail
    n = d.get("tool_count")
    # with no tool list, the derived rows carry the parent's reason, not a bare null
    err = (
        None
        if n
        else (d.get("error") or d.get("list_error") or "no tools in tools/list")
    )
    write(
        a.name + "_tools",
        {"value": n, "unit": "count", "detail": d, "error": err if n is None else None},
    )
    write(
        a.name + "_annotations_pct",
        {
            "value": round(100.0 * d["annotations"] / n, 1) if n else None,
            "unit": "%",
            "error": err,
        },
    )
    write(
        a.name + "_output_schema_pct",
        {
            "value": round(100.0 * d["output_schema"] / n, 1) if n else None,
            "unit": "%",
            "error": err,
        },
    )
    write(
        a.name + "_list_kb",
        {
            "value": round(d["tools_list_bytes"] / 1024, 2) if n else None,
            "unit": "KB",
            "error": err,
        },
    )
    write(
        a.name + "_kb_per_tool",
        {
            "value": round(d["bytes_per_tool"] / 1024, 3) if n else None,
            "unit": "KB",
            "error": err,
        },
    )
    clean = (
        (d.get("stdout_junk_bytes", 1) == 0)
        and bool(d.get("alive_after_list"))
        and "init_ms" in first
    )
    write(
        a.name + "_offline_clean",
        {
            "value": clean,
            "unit": "bool",
            "detail": {
                k: d.get(k)
                for k in (
                    "stdout_junk_lines",
                    "stdout_junk_bytes",
                    "alive_after_list",
                    "exit_code",
                    "error",
                )
            },
        },
    )


def cmd_egress(a):
    env = env_with(a.env)
    inp = session_file() if a.mcp_session else None
    # 1) strace: every connect() on an AF_INET/AF_INET6 socket, any destination
    fd, st = tempfile.mkstemp()
    os.close(fd)
    stdin = open(inp, "rb") if inp else subprocess.DEVNULL
    r = subprocess.run(
        [
            "timeout",
            "60",
            "strace",
            "-f",
            "-qq",
            "-e",
            "trace=connect,execve",
            "-o",
            st,
            "--",
        ]
        + a.cmd,
        env=env,
        stdin=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if inp:
        stdin.close()
    with open(st) as f:
        traced = f.readlines()
    lines = [ln for ln in traced if "connect(" in ln]
    strace_ok = any(
        "execve(" in ln for ln in traced
    )  # no execve line means ptrace was refused
    os.unlink(st)
    inet = [ln for ln in lines if "sa_family=AF_INET" in ln]
    ports = sorted({int(m) for ln in inet for m in re.findall(r"htons\((\d+)\)", ln)})
    # 2) hang proxy: proxy env points at a listener that accepts and never answers
    proxy = HangProxy()
    proxy.start()
    purl = "http://127.0.0.1:%d" % PROXY_PORT
    penv = dict(env)
    for k in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        penv[k] = purl
    wrapped = ["timeout", "60"] + a.cmd
    warm, wcodes = hyperfine(wrapped, a.runs, 2, env=env, input_path=inp)
    black, _ = hyperfine(wrapped, a.runs, 0, env=penv, input_path=inp)
    w, b = median(warm), median(black)
    limit = max(25.0, 0.2 * w)
    row = {
        "value": len(inet) + proxy.accepts,
        "unit": "count",
        "method": "strace -f -e trace=connect (AF_INET/AF_INET6, any address) + hang-proxy accept count; backstop: hyperfine %d runs"
        % a.runs,
        "connects_inet": len(inet),
        "connect_ports": ports,
        "connects_other_families": len(lines) - len(inet),
        "proxy_accepts": proxy.accepts,
        "proxy_accepts_per_run": round(proxy.accepts / max(1, a.runs), 2),
        "strace_ran": strace_ok,
        "exit_code": r.returncode,
        "warm_ms": round(w, 3),
        "blackholed_ms": round(b, 3),
        "delta_ms": round(b - w, 3),
        "backstop_limit_ms": round(limit, 3),
        "backstop_ok": (b - w) <= limit,
        "cmd": a.cmd,
    }
    delta = {
        "value": row["delta_ms"],
        "unit": "ms",
        "limit": row["backstop_limit_ms"],
        "ok": row["backstop_ok"],
    }
    if not strace_ok:
        row["value"], row["error"] = (
            None,
            "strace could not trace the command (ptrace refused?)",
        )
    elif bad_codes([r.returncode], a.ok_codes):
        row["value"], row["error"] = None, "exit code %d" % r.returncode
    if bad_codes(wcodes, a.ok_codes):
        delta["value"], delta["error"] = (
            None,
            "exit codes %s" % bad_codes(wcodes, a.ok_codes),
        )
    write(a.name, row)
    write(a.name + "_delta_ms", delta)


def _policy():
    with open(GATES) as f:
        return json.load(f)["imports"]


IMPORT_RE = re.compile(r"^import time:\s+\d+\s+\|\s+\d+\s+\|\s?(\s*)(\S+)\s*$")


def classify(modules, policy, path):
    std = set(sys.stdlib_module_names)
    allow = policy["allowlist"]
    fp_prefixes = tuple(policy["first_party_prefixes"])
    first_party = [m for m in modules if m.split(".")[0].startswith(fp_prefixes)]
    third = sorted(
        {
            m.split(".")[0]
            for m in modules
            if m.split(".")[0] not in std
            and m not in first_party
            and m not in allow["extra"]
        }
    )
    forb = sorted(
        {
            f
            for f in policy["forbidden"].get(path, [])
            for m in modules
            if m == f or m.startswith(f + ".")
        }
    )
    viol = []
    if path in allow["paths"]:
        excl_top = set(allow["stdlib_exclude"])
        dotted = tuple(allow["dotted_exclude"])
        for m in modules:
            top = m.split(".")[0]
            if m in allow["extra"]:
                continue
            if top.startswith(fp_prefixes):
                if m not in allow["first_party"]:
                    viol.append(m)
                continue
            if (
                top not in std
                or top in excl_top
                or any(m == d or m.startswith(d + ".") for d in dotted)
            ):
                viol.append(m)
    return {
        "modules": len(modules),
        "first_party_modules": len(first_party),
        "third_party_top_level": third,
        "forbidden_hits": forb,
        "allowlist_checked": path in allow["paths"],
        "allowlist_violations": len(viol),
        "allowlist_violation_sample": viol[:25],
    }


def cmd_imports(a):
    env = env_with(a.env)
    env["PYTHONPROFILEIMPORTTIME"] = "1"
    inp = session_file() if a.mcp_session else None
    stdin = open(inp, "rb") if inp else subprocess.DEVNULL
    r = subprocess.run(
        ["timeout", "60"] + a.cmd,
        env=env,
        stdin=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    mods = []
    for ln in r.stderr.splitlines():
        m = IMPORT_RE.match(ln)
        if m:
            mods.append(m.group(2))
    res = classify(mods, _policy(), a.path)
    res["exit_code"] = r.returncode
    res["cmd"] = a.cmd
    res["method"] = (
        "PYTHONPROFILEIMPORTTIME=1 through the registered command; every module imported during the process"
    )
    err = None
    if not mods:
        # fail closed: an interpreter started with -I/-E ignores PYTHONPROFILEIMPORTTIME
        err = "no import-time lines (interpreter ignored PYTHONPROFILEIMPORTTIME?)"
    elif bad_codes([r.returncode], a.ok_codes):
        # a crash part-way through hides whatever it would have imported next
        err = "exit code %d" % r.returncode
    write(
        a.name,
        dict(
            res,
            value=None if err else len(res["forbidden_hits"]),
            unit="count",
            error=err,
        ),
    )
    if res["allowlist_checked"]:
        write(
            a.name + "_allowlist",
            {
                "value": None if err else res["allowlist_violations"],
                "unit": "count",
                "sample": res["allowlist_violation_sample"],
                "error": err,
            },
        )


QUOTED_PY_RE = re.compile(r"^# code object from '[^']*\.py'$")
SOURCE_RE = re.compile(r"^# code object from [^'].*\.py$")


def cmd_verbose(a):
    env = env_with(a.env)
    env["PYTHONVERBOSE"] = "1"
    inp = session_file() if a.mcp_session else None
    stdin = open(inp, "rb") if inp else subprocess.DEVNULL
    r = subprocess.run(
        ["timeout", "60"] + a.cmd,
        env=env,
        stdin=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    lines = r.stderr.splitlines()
    src = [ln for ln in lines if SOURCE_RE.match(ln)]
    quoted = [ln for ln in lines if QUOTED_PY_RE.match(ln)]
    pyc = [
        ln
        for ln in lines
        if ln.startswith("# code object from '") and ln.endswith(".pyc'")
    ]
    err = (
        None
        if (src or pyc)
        else "no code-object lines (interpreter ignored PYTHONVERBOSE?)"
    )
    if err is None and bad_codes([r.returncode], a.ok_codes):
        err = "exit code %d" % r.returncode
    write(
        a.name,
        {
            "value": None if err else len(src),
            "error": err,
            "unit": "count",
            "method": "PYTHONVERBOSE=1, first run in a fresh container; counts '# code object from <path>.py' (unquoted = compiled from source)",
            "source_compiles": len(src),
            "pyc_loads": len(pyc),
            "quoted_py_matches": len(quoted),
            "sample": [ln[len("# code object from ") :] for ln in src[:15]],
            "exit_code": r.returncode,
            "cmd": a.cmd,
        },
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="sub", required=True)

    def common(p, runs=30, warmup=3):
        p.add_argument("--name", required=True)
        p.add_argument("--runs", type=int, default=runs)
        p.add_argument("--warmup", type=int, default=warmup)
        p.add_argument("--env", action="append", default=[])
        p.add_argument(
            "--mcp-session",
            action="store_true",
            help="feed initialize+initialized+tools/list on stdin",
        )
        p.add_argument(
            "--ok-codes",
            type=lambda v: [int(x) for x in v.split(",")],
            default=[0],
            help="exit codes that count as a valid run (default 0)",
        )
        p.add_argument("cmd", nargs=argparse.REMAINDER)

    p = sub.add_parser("hf")
    common(p)
    p.add_argument(
        "--serve",
        type=int,
        default=None,
        help="start the fake API with this many words",
    )
    p.add_argument("--make-file", action="append", default=[])
    p.add_argument("--capture-bytes", action="store_true")
    p.add_argument(
        "--require-json",
        action="store_true",
        help="with --capture-bytes: stdout must be non-empty JSON",
    )
    p = sub.add_parser("rss")
    common(p, runs=3)
    p.add_argument("--serve", type=int, default=None)
    p.add_argument("--make-file", action="append", default=[])
    p = sub.add_parser("mcp")
    common(p)
    p.add_argument("--timeout", type=float, default=15.0)
    p = sub.add_parser("egress")
    common(p, runs=3)
    p = sub.add_parser("imports")
    common(p)
    p.add_argument("--path", required=True)
    p = sub.add_parser("verbose")
    common(p)
    a = ap.parse_args(argv)
    if a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    if not a.cmd:
        ap.error("missing command after --")
    {
        "hf": cmd_hf,
        "rss": cmd_rss,
        "mcp": cmd_mcp,
        "egress": cmd_egress,
        "imports": cmd_imports,
        "verbose": cmd_verbose,
    }[a.sub](a)


if __name__ == "__main__":
    main()
