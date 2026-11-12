# Benchmark harness

Measures `dg` against a pinned set of other CLIs and MCP servers, and gates
the results against `gates.json`. Every measured process runs in a Docker
container started with `--network none`; the only privileged container is a
helper that drops the page cache before cold rows. Docker is the only
requirement. Cold rows refuse to start the privileged helper in CI unless the
runner is GitHub-hosted. The orchestrator (`bench.py`) needs Python 3.9+ and only drives Docker.

```sh
bench/run.sh                               # dg at HEAD plus every pinned competitor
bench/run.sh --subjects dg --ref v0.4.0    # dg only, any git ref
bench/run.sh --subjects dg,voice --quick   # 10 runs per timing row instead of 30
python3 bench/check.py bench/out/<run>/results.json [--baseline bench/baselines/<label>.json]
python3 bench/bench.py pair --base origin/main --head HEAD    # interleaved base vs head
python3 bench/check.py pair bench/out/<run>/pair.json
python3 bench/check.py list                # every gate, target and since version
bench/run.sh --profile release             # dg only, no timing gates: deterministic, size, MCP-shape and RSS rows
bench/selftest.sh                          # harness self-test against a fake CLI
```

## What is measured

| Row | How |
|---|---|
| `version_warm`, `help_warm`, `subhelp_warm` | `hyperfine -N --warmup 3 --runs 30` |
| `help_cold` | page cache dropped before each of 10 runs; `hyperfine -N --runs 1` |
| `mcp_init`, `mcp_list` | spawn → `initialize` result, spawn → `tools/list` result (30 runs) |
| `mcp_tools`, `mcp_annotations_pct`, `mcp_output_schema_pct`, `mcp_list_kb`, `mcp_kb_per_tool` | from the `tools/list` result |
| `mcp_offline_clean` | no non-JSON-RPC bytes on stdout and the server still alive after `tools/list` |
| `egress_*` | `strace -f -e trace=connect` count of AF_INET/AF_INET6 sockets plus accepts on a proxy that never answers; must be 0 |
| `egress_*_delta_ms` | timing backstop: blackholed proxy vs warm, ≤ max(25 ms, 0.2 × warm) |
| `imports_*` | modules imported on a path (`PYTHONPROFILEIMPORTTIME`), against the allowlist and forbidden lists in `gates.json` |
| `norecompile_*` | `PYTHONVERBOSE` lines for modules compiled from source on the first run; must be 0 |
| `json_50k_words` | against `lib/fake_dg.py` on loopback; stdout must be a non-empty JSON document |
| `rss_prerecorded_50mb`, `rss_prerecorded_500mb` | peak RSS uploading a silent WAV of that size to the fake API |
| `install_mb` | `du -sm` of the install root, bytecode included |
| `floor_*` | interpreter and import floors for the Python path |

A row whose command exits non-zero (outside the row's accepted codes) records
an error instead of a value, so a crash is never a fast run. The `imports_*`
and `norecompile_*` rows read `PYTHONPROFILEIMPORTTIME` and `PYTHONVERBOSE`
output; an interpreter started with `-I` or `-E` ignores those, and the rows
then record an error rather than 0. A launcher that isolates its interpreter
needs a way to pass those variables through before these rows can measure it.
The egress row also errors when strace can't trace the command.

Every Python image has its stdlib compiled to `.pyc`, and `dg` is installed with
`uv tool install --compile-bytecode` from wheels built out of a clean
`git archive` of the ref, with third-party versions from `uv.lock`, so a gate
moves only when the ref changes. `--deps latest` resolves third-party versions
from the index instead, which is what a user's install gets on the day.

## Gates

Each gate in `gates.json` quotes its target and has a `since` version.

- A gate is **active** once the version under test reaches `since`. Inactive
  gates are measured and shown as pending.
- **Timing gates are report-only** until `"blocking": true` is set for them, per
  row, which happens after 30 scheduled A/A runs show at most one red and a +50 ms
  injected case fails. A row with more than one red gets its floor raised one
  step and its count restarted.
- **Every other gate blocks as soon as it is active**: zero egress, the import
  allowlist and forbidden lists, no source compiles, sizes, tool counts, peak RSS.
  An active blocking gate whose row is missing fails.
- **Regressions** are judged only on paired runs from one job: fail when the
  median of head − base over the rounds exceeds max(10 %, 3 × MAD of the
  differences, the row floor), after one re-run. Comparisons with a stored
  baseline from another run are report-only.
- A paired row where head failed in most rounds is red; when the row is
  blocking, missing values on either side fail it.
- Competitor margins are same-run ratios (`best peer` column).

## Release gating

Not wired yet. `--profile release` measures dg only, with no competitors, no cold
rows and no warm or floor timing. It still runs report-only timing: the egress
backstop and one MCP timing run, bounded by a 60 s per-invocation timeout
(worst case about 32 minutes if every egress path stalls). Gates on a channel
listed in `planned_subjects` stay pending until the harness builds it; any
other subject a gate names that the harness doesn't know is an error.

Before a release job calls it:

- add a `workflow_call` trigger with a `ref` input (not `push: tags`: a tag release-please pushes with `GITHUB_TOKEN` starts no workflow);
- add a `profile` input to `competitive-bench.yml` and skip the timing self-test on that path;
- set a job timeout (about 30 minutes);
- the dg image build needs network (PyPI and the hyperfine download), so an outage fails the job;
- before 0.5.0, check that `dg mcp` exits 0 on stdin EOF offline, or the MCP import and no-recompile gates block on its exit code.

## Results

`results.json` (`schema: dg-bench/1`) holds the runner label, host, harness
commit and, per subject, its version and rows. A row is
`{"value", "unit", "median", "mad", "min", "max", "n", "times", "method", ...}`.
`pair.json` (`dg-bench/1-pair`) holds the per-round medians for base and head
plus head's deterministic rows. Baselines in `baselines/<runner-label>.json`
are results files without the raw samples. None is committed yet: the first
scheduled run on a runner writes one (`bench.py baseline`), and later ones are
refreshed by PR. `--baseline` is optional.

## Competitors

`competitors.json` pins every competitor's top-level version: sha256 for release
tarballs, an exact version for npm and PyPI, a commit for git installs, and a
digest for every base image. Transitive npm and PyPI dependencies and distro
tools (strace, GNU time) are resolved at build time; each result records the
strace version. Pins are arm64. Bump a pin by PR; that PR's run
is the measured delta.
