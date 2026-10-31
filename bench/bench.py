"""Benchmark orchestrator. Drives Docker only: every measured process runs inside
a bench container started with --network none. Stdlib only (Python 3.9+).

  python3 bench/bench.py run  [--ref REF] [--subjects dg,voice,...] [--label L] [--out DIR]
  python3 bench/bench.py pair --base REF --head REF [--rounds 10]
  python3 bench/bench.py collect OUT_DIR        # rebuild results.json from row files

`bench/run.sh` wraps `run`. `bench/check.py` evaluates the output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tarfile
import time

BENCH = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BENCH)
CFG = json.load(open(os.path.join(BENCH, "competitors.json")))
DRIVER = ["python3", "-I", "/bench/lib/measure.py"]
SCHEMA = "dg-bench/1"

# Data-path rows talk to the in-container fake API (loopback; the container has no network).
DATA_ENV = [
    "DEEPGRAM_API_KEY=fake_0000000000000000000000000000000000000000",
    "DEEPGRAM_PROJECT_ID=00000000-0000-0000-0000-000000000000",
    "DEEPGRAM_BASE_URL=http://127.0.0.1:8080",
    "DEEPCTL_TELEMETRY_DISABLED=1",
    "DO_NOT_TRACK=1",
]


def log(*a):
    print("[bench]", *a, file=sys.stderr, flush=True)


def sh(args, check=True, capture=False, **kw):
    r = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        **kw,
    )
    if check and r.returncode != 0:
        raise SystemExit(
            "command failed (%d): %s\n%s"
            % (r.returncode, " ".join(args), (r.stderr or "")[-2000:])
        )
    return r


def harness_commit():
    r = sh(["git", "-C", REPO, "rev-parse", "HEAD"], check=False, capture=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def git(*a):
    return sh(["git", "-C", REPO] + list(a), capture=True).stdout.strip()


# ----------------------------------------------------------------- images
def _hf_args():
    h = CFG["hyperfine"]
    return [
        "--build-arg",
        "HYPERFINE_VERSION=" + h["version"],
        "--build-arg",
        "HYPERFINE_SHA_ARM64=" + h["sha256"]["arm64"],
        "--build-arg",
        "HYPERFINE_SHA_AMD64=" + h["sha256"]["amd64"],
    ]


def build_fake(ref):
    """Self-test subject, e.g. 'fake:delay=5,egress=true,fail=true,imports=asyncio,nopyc=true,version=0.6.0'
    (bench/tests/fake)."""
    opts = dict(kv.split("=", 1) for kv in ref[len("fake:") :].split(",") if kv)
    knobs = {
        "DELAY_MS": opts.get("delay", "0"),
        "EGRESS": opts.get("egress", "false"),
        "VERSION": opts.get("version", "0.0.0"),
        "FAIL": opts.get("fail", "false"),
        "IMPORTS": opts.get("imports", ""),
        "NOPYC": opts.get("nopyc", "false"),
        "NOPYC_WRITABLE": opts.get("nopyc_writable", "false"),
    }
    tag = (
        "dg-bench-fake:"
        + hashlib.sha1(json.dumps(knobs, sort_keys=True).encode()).hexdigest()[:12]
    )
    args = []
    for k, v in sorted(knobs.items()):
        args += ["--build-arg", "%s=%s" % (k, v)]
    sh(
        [
            "docker",
            "build",
            "-q",
            "-t",
            tag,
            "-f",
            os.path.join(BENCH, "docker", "Dockerfile.fake"),
            "--build-context",
            "fakectx=" + os.path.join(BENCH, "tests", "fake"),
            "--build-arg",
            "PY_IMAGE=" + CFG["images"]["python"],
        ]
        + args
        + _hf_args()
        + [os.path.join(BENCH, "docker")],
        capture=True,
    )
    return tag, ref


DG_DEPS = os.environ.get("BENCH_DG_DEPS", "lock")


def build_dg(ref, workdir):
    """Build the dg image from a clean `git archive` of REF (or a source dir)."""
    if ref.startswith("fake:"):
        return build_fake(ref)
    if os.path.isdir(ref):
        src = os.path.abspath(ref)
        sha = "dir-" + hashlib.sha1(src.encode()).hexdigest()[:10]
    else:
        sha = git("rev-parse", ref + "^{commit}")
        src = os.path.join(workdir, "src-" + sha[:12])
        if not os.path.isdir(src):
            tf = src + ".tar"
            sh(["git", "-C", REPO, "archive", "--format=tar", "-o", tf, sha])
            os.makedirs(src)
            with tarfile.open(tf) as t:
                t.extractall(src)
            os.unlink(tf)
    tag = "dg-bench-dg:" + sha[:12] + ("" if DG_DEPS == "lock" else "-" + DG_DEPS)
    img = CFG["images"]
    log("build", tag, "from", ref)
    sh(
        [
            "docker",
            "build",
            "-q",
            "-t",
            tag,
            "-f",
            os.path.join(BENCH, "docker", "Dockerfile.dg"),
            "--build-context",
            "benchctx=" + os.path.join(BENCH, "docker"),
            "--build-arg",
            "PY_IMAGE=" + img["python"],
            "--build-arg",
            "UV_IMAGE=" + img["uv"],
            "--build-arg",
            "DEPS=" + DG_DEPS,
        ]
        + _hf_args()
        + [src],
        capture=True,
    )
    return tag, sha


def build_competitor(name):
    s = CFG["subjects"][name]
    img = CFG["images"]
    tag = "dg-bench-%s:%s" % (name, s["version"][:20].replace("+", "_"))
    d = os.path.join(BENCH, "docker")
    args = ["docker", "build", "-q", "-t", tag] + _hf_args()
    if s["kind"] == "bin":
        args += [
            "-f",
            os.path.join(d, "Dockerfile.bin"),
            "--build-arg",
            "BASE_IMAGE=" + img["debian"],
            "--build-arg",
            "NAME=" + name,
            "--build-arg",
            "URL=" + s["url"],
            "--build-arg",
            "SHA256=" + s["sha256"],
            "--build-arg",
            "VERSION=" + s["version"],
        ]
    elif s["kind"] == "npm":
        args += [
            "-f",
            os.path.join(d, "Dockerfile.npm"),
            "--build-arg",
            "BASE_IMAGE=" + img["node"],
            "--build-arg",
            "PACKAGE=" + s["package"],
            "--build-arg",
            "VERSION=" + s["version"],
        ]
    elif s["kind"] == "uv":
        args += [
            "-f",
            os.path.join(d, "Dockerfile.uv"),
            "--build-arg",
            "PY_IMAGE=" + img[s.get("python_image", "python")],
            "--build-arg",
            "UV_IMAGE=" + img["uv"],
            "--build-arg",
            "SPEC=" + s["spec"],
            "--build-arg",
            "VERSION=" + s["version"],
        ]
    elif s["kind"] == "image":
        return s["image"]
    else:
        raise SystemExit("unknown kind " + s["kind"])
    log("build", tag)
    r = sh(args + [d], check=False, capture=True)
    if r.returncode != 0:
        log("BUILD FAILED", name, r.stderr[-800:])
        return None
    return tag


# ----------------------------------------------------------------- containers
class Box:
    """A long-lived bench container for one subject (always --network none)."""

    def __init__(self, image, name, outdir):
        self.name = name
        self.outdir = outdir
        for d in (outdir, os.path.join(outdir, "cold")):
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777)  # the container user is not the host user
        sh(["docker", "rm", "-f", name], check=False, capture=True)
        sh(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--network",
                "none",
                "--name",
                name,
                "-v",
                BENCH + ":/bench:ro",
                "-v",
                outdir + ":/out",
                image,
                "sleep",
                "infinity",
            ],
            capture=True,
        )

    def exec(self, args, check=False, env=()):
        e = []
        for kv in env:
            e += ["-e", kv]
        return sh(
            ["docker", "exec"] + e + [self.name] + args, check=check, capture=True
        )

    def drive(self, sub, name, cmd, *opts):
        r = self.exec(DRIVER + [sub, "--name", name] + list(opts) + ["--"] + cmd)
        line = (r.stdout or "").strip().splitlines()
        log(
            self.name,
            (line[-1] if line else "")
            or ("rc=%d %s" % (r.returncode, (r.stderr or "")[-400:])),
        )
        if r.returncode != 0:
            with open(os.path.join(self.outdir, name + ".json"), "w") as f:
                json.dump(
                    {"row": name, "value": None, "error": (r.stderr or "")[-600:]}, f
                )

    def meta(self):
        r = self.exec(["cat", "/opt/bench/meta.json"])
        meta = json.loads(r.stdout) if r.returncode == 0 else {}
        st = self.exec(["cat", "/opt/bench/strace.txt"])
        if st.returncode == 0:
            meta["strace"] = st.stdout.strip()
        return meta

    def close(self):
        sh(["docker", "rm", "-f", self.name], check=False, capture=True)


def drop_caches():
    # The helper needs --privileged. GitHub-hosted runners are throwaway VMs where the
    # job already has root; anywhere else in CI (a self-hosted runner) refuse.
    if os.environ.get("CI") and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit(
            "cold rows need a privileged container; refusing outside GitHub-hosted runners (use --cold-runs 0)"
        )
    sh(
        [
            "docker",
            "run",
            "--rm",
            "--privileged",
            "--network",
            "none",
            CFG["images"]["dropcaches"],
            "sh",
            "-c",
            "sync; echo 3 > /proc/sys/vm/drop_caches",
        ],
        capture=True,
    )


def cold_row(box, row, cmd, runs):
    """First run after the page cache is dropped (VM-wide, from a helper container);
    timed by hyperfine inside the measured container so no interpreter warms the cache first."""
    for i in range(runs):
        drop_caches()
        box.exec(
            [
                "hyperfine",
                "-N",
                "-i",
                "--style",
                "none",
                "--runs",
                "1",
                "--export-json",
                "/out/cold/%s_%02d.json" % (row, i),
                "--",
                " ".join(cmd),
            ]
        )
    log(box.name, row, "cold x%d done" % runs)


# ----------------------------------------------------------------- row plans
def run_dg(box, s, quick=False, cold_runs=10, data=True, timing=True):
    """timing=False is the release profile: deterministic, size, MCP-shape and RSS rows only."""
    cli, mcp = s["cli"], s["mcp"]
    menv = ["--env=%s=%s" % kv for kv in sorted(mcp.get("env", {}).items())]
    runs = ["--runs", "10", "--warmup", "2"] if quick else []
    # deterministic rows first: the no-recompile check wants the first runs in a fresh container
    for path in ("version", "help"):
        box.drive("verbose", "norecompile_" + path, cli[path])
    box.drive("verbose", "norecompile_mcp", mcp["cmd"], "--mcp-session", *menv)
    for path in ("version", "help", "subhelp"):
        box.drive("imports", "imports_" + path, cli[path], "--path", path)
    box.drive(
        "imports", "imports_mcp", mcp["cmd"], "--path", "mcp", "--mcp-session", *menv
    )
    for path in ("version", "help", "subhelp"):
        box.drive("egress", "egress_" + path, cli[path])
    box.drive("egress", "egress_mcp", mcp["cmd"], "--mcp-session", *menv)
    if not timing:
        runs = [
            "--runs",
            "1",
            "--warmup",
            "0",
        ]  # MCP rows for tool counts and sizes only
    # warm timing
    for path in ("version", "help", "subhelp") if timing else ():
        box.drive("hf", path + "_warm", cli[path], *runs)
    box.drive("mcp", "mcp", mcp["cmd"], *(menv + runs))
    box.drive(
        "mcp",
        "mcp_read",
        mcp["cmd"] + ["--toolsets", "read"],
        "--runs",
        "3",
        "--warmup",
        "0",
        *menv,
    )
    py = s["python"]
    floors = [
        ("floor_python_pass", [py, "-c", "pass"]),
        ("floor_python_isolated", [py, "-I", "-S", "-c", "pass"]),
        ("floor_import_click", [py, "-c", "import click"]),
        (
            "floor_import_click_platformdirs_yaml",
            [py, "-c", "import click, platformdirs, yaml"],
        ),
        ("floor_import_click_rich_console", [py, "-c", "import click, rich.console"]),
        ("floor_import_deepctl_core_config", [py, "-c", "import deepctl_core.config"]),
        ("floor_import_deepctl_core", [py, "-c", "import deepctl_core"]),
    ]
    for name, cmd in floors if timing else ():
        box.drive(
            "hf", name, cmd, *(["--runs", "10", "--warmup", "2"] if quick else [])
        )
    denv = ["--env=" + kv for kv in DATA_ENV]
    if data and timing:
        box.drive(
            "hf",
            "json_50k_words",
            [cli["version"][0], "-o", "json", "listen", "/tmp/s.wav"],
            "--serve",
            "50000",
            "--runs",
            "3" if quick else "5",
            "--warmup",
            "1",
            "--capture-bytes",
            "--require-json",
            "--make-file",
            "/tmp/s.wav:64K",
            *denv,
        )
    if data:
        box.drive(
            "rss",
            "rss_prerecorded_500mb",
            [cli["version"][0], "listen", "/tmp/f500.wav"],
            "--serve",
            "50",
            "--runs",
            "3",
            "--make-file",
            "/tmp/f500.wav:500M",
            *denv,
        )
        box.drive(
            "rss",
            "rss_prerecorded_50mb",
            [cli["version"][0], "listen", "/tmp/f50.wav"],
            "--serve",
            "50",
            "--runs",
            "3",
            "--make-file",
            "/tmp/f50.wav:50M",
            *denv,
        )
        box.exec(["rm", "-f", "/tmp/f500.wav", "/tmp/f50.wav", "/tmp/s.wav"])
    if cold_runs:
        cold_row(box, "help_cold", cli["help"], cold_runs)


def run_competitor(box, s, quick=False, cold_runs=10):
    runs = ["--runs", "10", "--warmup", "2"] if quick else []
    cli = s.get("cli")
    if cli:
        for path in ("version", "help"):
            box.drive("hf", path + "_warm", cli[path], *runs)
        box.drive("egress", "egress_version", cli["version"])
    if s.get("mcp"):
        menv = ["--env=%s=%s" % kv for kv in sorted(s["mcp"].get("env", {}).items())]
        box.drive("mcp", "mcp", s["mcp"]["cmd"], *(menv + runs))
    if cli and cold_runs:
        cold_row(box, "help_cold", cli["help"], cold_runs)


# ----------------------------------------------------------------- results
def host_info():
    r = sh(["docker", "info", "--format", "{{json .}}"], capture=True, check=False)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {}
    return {
        "docker_os": d.get("OperatingSystem"),
        "kernel": d.get("KernelVersion"),
        "arch": d.get("Architecture"),
        "cpus": d.get("NCPU"),
        "mem_gb": round((d.get("MemTotal") or 0) / 2**30, 1),
        "server_version": d.get("ServerVersion"),
        "orchestrator_platform": platform.platform(),
    }


def default_label():
    return (
        os.environ.get("BENCH_RUNNER_LABEL") or "local-%s" % platform.machine().lower()
    )


def collect(outdir):
    meta = json.load(open(os.path.join(outdir, "run.json")))
    subjects = {}
    for name, sm in meta["subjects"].items():
        d = os.path.join(outdir, name)
        rows = {}
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            r = json.load(open(f))
            rows[r.pop("row", os.path.basename(f)[:-5])] = r
        colds, cold_codes = [], set()
        for f in sorted(glob.glob(os.path.join(d, "cold", "*.json"))):
            res = json.load(open(f))["results"][0]
            colds.append(res["times"][0] * 1000)
            cold_codes.update(res.get("exit_codes") or [])
        if colds:
            m = statistics.median(colds)
            rows["help_cold"] = {
                "value": round(m, 3),
                "unit": "ms",
                "median": round(m, 3),
                "mad": round(statistics.median([abs(x - m) for x in colds]), 3),
                "min": round(min(colds), 3),
                "max": round(max(colds), 3),
                "n": len(colds),
                "times": [round(x, 3) for x in colds],
                "method": "page cache dropped before each run; hyperfine -N --runs 1",
                "exit_codes": sorted(cold_codes),
            }
            if cold_codes - {0}:
                rows["help_cold"]["value"] = None
                rows["help_cold"]["error"] = "exit codes %s" % sorted(cold_codes - {0})
        im = sm.get("image_meta") or {}
        if "install_mb" in im:
            rows["install_mb"] = {
                "value": im["install_mb"],
                "unit": "MB",
                "method": "du -sm of the install root, with .pyc",
                "dists": im.get("dists"),
            }
        subjects[name] = {k: v for k, v in sm.items() if k != "image_meta"}
        subjects[name]["version"] = im.get("version", sm.get("version"))
        if im.get("strace"):
            subjects[name]["strace"] = im["strace"]
        if im.get("deps"):
            subjects[name]["deps"] = im["deps"]
        subjects[name]["rows"] = rows
    doc = dict(meta, subjects=subjects)
    with open(os.path.join(outdir, "results.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    return doc


def expand(names):
    out = []
    for n in names:
        for x in CFG["groups"].get(n, [n]):
            if x not in CFG["subjects"]:
                raise SystemExit("unknown subject " + x)
            if x not in out:
                out.append(x)
    return out


def cmd_run(a):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = a.label or default_label()
    outdir = os.path.abspath(a.out or os.path.join(BENCH, "out", stamp + "-" + label))
    os.makedirs(outdir, exist_ok=True)
    work = os.path.join(BENCH, "out", ".src")
    os.makedirs(work, exist_ok=True)
    if a.profile == "release":
        # A release gate measures our own deterministic, size and resource rows:
        # no competitors, no cold rows (no privileged helper), no timing.
        a.subjects, a.cold_runs = "dg", 0
    names = expand(a.subjects.split(","))
    meta = {
        "schema": SCHEMA,
        "created": stamp,
        "runner_label": label,
        "host": host_info(),
        "harness_commit": harness_commit(),
        "hyperfine": CFG["hyperfine"]["version"],
        "quick": a.quick,
        "profile": a.profile,
        "subjects": {},
    }
    for name in names:
        s = CFG["subjects"][name]
        t0 = time.time()
        if s["kind"] == "dg":
            image, sha = build_dg(a.ref, work)
            entry = {"kind": "dg", "ref": sha, "image": image}
        else:
            image = build_competitor(name)
            entry = {"kind": s["kind"], "version": s["version"], "image": image}
            if image is None:
                entry["error"] = "image build failed"
                meta["subjects"][name] = entry
                continue
        box = Box(
            image, "dg-bench-%s-%s" % (stamp.lower(), name), os.path.join(outdir, name)
        )
        try:
            entry["image_meta"] = box.meta()
            if s["kind"] == "dg":
                run_dg(
                    box,
                    s,
                    quick=a.quick,
                    cold_runs=a.cold_runs,
                    data=not a.skip_data,
                    timing=a.profile != "release",
                )
            else:
                run_competitor(box, s, quick=a.quick, cold_runs=a.cold_runs)
        finally:
            box.close()
        entry["wall_s"] = round(time.time() - t0, 1)
        meta["subjects"][name] = entry
        with open(os.path.join(outdir, "run.json"), "w") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
    with open(os.path.join(outdir, "run.json"), "w") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    collect(outdir)
    print(os.path.join(outdir, "results.json"))


def cmd_pair(a):
    """Base and head timed interleaved in one job. Deterministic rows run on head, first."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.abspath(a.out or os.path.join(BENCH, "out", stamp + "-pair"))
    work = os.path.join(BENCH, "out", ".src")
    os.makedirs(work, exist_ok=True)
    s = CFG["subjects"]["dg"]
    imgs = {}
    for side, ref in (("base", a.base), ("head", a.head)):
        imgs[side] = build_dg(ref, work)
    boxes = {
        side: Box(
            imgs[side][0],
            "dg-bench-%s-%s" % (stamp.lower(), side),
            os.path.join(outdir, side),
        )
        for side in ("base", "head")
    }
    menv = ["--env=%s=%s" % kv for kv in sorted(s["mcp"].get("env", {}).items())]
    rows = {
        "version_warm": ("hf", s["cli"]["version"], []),
        "help_warm": ("hf", s["cli"]["help"], []),
        "subhelp_warm": ("hf", s["cli"]["subhelp"], []),
        "mcp_init": ("mcp", s["mcp"]["cmd"], menv),
    }
    if a.rows:
        rows = {k: v for k, v in rows.items() if k in a.rows.split(",")}
    rounds = {r: {"base": [], "head": []} for r in rows}
    try:
        # Deterministic rows first, on a fresh head container: the no-recompile
        # check has to see the first runs, before warm rounds cache any .pyc.
        if not a.skip_deterministic:
            hb = boxes["head"]
            for path in ("version", "help"):
                hb.drive("verbose", "norecompile_" + path, s["cli"][path])
            hb.drive(
                "verbose", "norecompile_mcp", s["mcp"]["cmd"], "--mcp-session", *menv
            )
            for path in ("version", "help", "subhelp"):
                hb.drive("imports", "imports_" + path, s["cli"][path], "--path", path)
                hb.drive("egress", "egress_" + path, s["cli"][path])
            hb.drive(
                "imports",
                "imports_mcp",
                s["mcp"]["cmd"],
                "--path",
                "mcp",
                "--mcp-session",
                *menv,
            )
            hb.drive("egress", "egress_mcp", s["mcp"]["cmd"], "--mcp-session", *menv)
            hb.drive(
                "mcp", "mcp", s["mcp"]["cmd"], "--runs", "3", "--warmup", "0", *menv
            )
        for i in range(a.rounds):
            order = ("base", "head") if i % 2 == 0 else ("head", "base")
            for row, (sub, cmd, extra) in rows.items():
                for side in order:
                    name = "r%02d_%s" % (i, "mcp" if sub == "mcp" else row)
                    boxes[side].drive(
                        sub, name, cmd, "--runs", str(a.runs), "--warmup", "2", *extra
                    )
                    fn = name + ("_init" if sub == "mcp" else "") + ".json"
                    v = json.load(open(os.path.join(outdir, side, fn))).get("value")
                    rounds[row][side].append(v)
        meta = {}
        for side in ("base", "head"):
            meta[side] = {
                "ref": imgs[side][1],
                "image": imgs[side][0],
                "image_meta": boxes[side].meta(),
            }
    finally:
        for b in boxes.values():
            b.close()
    head_rows = {}
    for f in glob.glob(os.path.join(outdir, "head", "*.json")):
        if not os.path.basename(f).startswith("r"):
            r = json.load(open(f))
            head_rows[r.pop("row")] = r
    doc = {
        "schema": SCHEMA + "-pair",
        "created": stamp,
        "runner_label": a.label or default_label(),
        "host": host_info(),
        "harness_commit": harness_commit(),
        "rounds_n": a.rounds,
        "runs_per_round": a.runs,
        "base": {
            "ref": meta["base"]["ref"],
            "version": meta["base"]["image_meta"].get("version"),
        },
        "head": {
            "ref": meta["head"]["ref"],
            "version": meta["head"]["image_meta"].get("version"),
            "rows": head_rows,
        },
        "rounds": rounds,
    }
    path = os.path.join(outdir, "pair.json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    print(path)


def cmd_baseline(a):
    """Write baselines/<runner-label>.json: a results file without raw samples."""
    doc = json.load(open(a.results))
    drop = (
        "times",
        "cmd",
        "sample",
        "allowlist_violation_sample",
        "wall_ms",
    )
    for s in doc["subjects"].values():
        for r in (s.get("rows") or {}).values():
            for k in drop:
                r.pop(k, None)
    doc["source_results"] = os.path.relpath(os.path.abspath(a.results), REPO)
    path = os.path.join(BENCH, "baselines", doc["runner_label"] + ".json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(path)


def main(argv=None):
    if shutil.which("docker") is None:
        raise SystemExit("docker is required")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument(
        "--ref", default="HEAD", help="git ref (or source directory) for the dg subject"
    )
    p.add_argument("--subjects", default="dg,all,mcp")
    p.add_argument("--label")
    p.add_argument("--out")
    p.add_argument(
        "--quick", action="store_true", help="10 runs per timing row instead of 30"
    )
    p.add_argument(
        "--cold-runs",
        type=int,
        default=10,
        help="0 skips cold rows (they drop the VM page cache)",
    )
    p.add_argument("--skip-data", action="store_true")
    p.add_argument(
        "--profile",
        choices=("full", "release"),
        default="full",
        help="release: dg only; deterministic, size and RSS rows; no cold or timing rows",
    )
    p.add_argument(
        "--deps",
        choices=("lock", "latest"),
        help="dg third-party versions: uv.lock (default) or latest from the index",
    )
    p = sub.add_parser("pair")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--rows")
    p.add_argument("--label")
    p.add_argument("--out")
    p.add_argument("--skip-deterministic", action="store_true")
    p = sub.add_parser("collect")
    p.add_argument("outdir")
    p = sub.add_parser("baseline")
    p.add_argument("results")
    a = ap.parse_args(argv)
    global DG_DEPS
    if getattr(a, "deps", None):
        DG_DEPS = a.deps
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "pair":
        cmd_pair(a)
    elif a.cmd == "baseline":
        cmd_baseline(a)
    else:
        collect(os.path.abspath(a.outdir))


if __name__ == "__main__":
    main()
