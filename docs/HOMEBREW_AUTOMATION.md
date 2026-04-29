# Homebrew formula automation

How `deepgram/homebrew-tap/Formula/deepgram.rb` stays in sync with `deepctl` releases.

## What this does

Every time `deepctl` ships a new root release (`v0.2.18`, `v0.3.0`, etc.) from this repo, a GitHub Actions job:

1. Waits for the new version to land on PyPI
2. Mints a short-lived installation token for `deepgram/homebrew-tap` via a GitHub App
3. Regenerates `Formula/deepgram.rb` from [`scripts/templates/deepgram.rb.template`](../scripts/templates/deepgram.rb.template) using fresh PyPI metadata + transitive resource blocks generated via `homebrew-pypi-poet`
4. Pushes a `bump-deepctl-X.Y.Z` branch and opens a PR back to the tap

The job is gated on **root** `v*` tags only — sub-package tags like `deepctl-cmd-listen-v0.0.3` are skipped, since they don't change what `pip install deepctl` resolves to.

## Files

| Path | Purpose |
|---|---|
| `scripts/templates/deepgram.rb.template` | Source of truth for the formula. Hand-edit to change the formula header, deps, install steps, or test block. |
| `scripts/bump_brew_formula.py` | Reads the template, fetches PyPI metadata, runs `homebrew-pypi-poet`, writes the rendered formula to a target path. |
| `.github/workflows/release.yml` (`bump-brew-formula` job) | Wires the script up to release-please and the GitHub App. |

## Manual / emergency bump

To regenerate the formula locally — useful for testing template changes, or pushing a hotfix without waiting for a release:

```bash
git clone https://github.com/deepgram/homebrew-tap.git
python scripts/bump_brew_formula.py \
    --version 0.2.18 \
    --formula homebrew-tap/Formula/deepgram.rb
```

The script needs Python 3.13 available as `python3.13` on PATH (override with `--python`). It creates an isolated venv per run; no global pollution. Add `--dry-run` to print to stdout instead of writing.

The output is **deterministic** for a given `(version, python_exe)` pair — running twice produces byte-identical files. This is the primary correctness check.

## GitHub App setup (one-time, security-team)

The cross-repo PR creation requires a credential the default `GITHUB_TOKEN` cannot provide. We use an **org-owned GitHub App**, not a person-bound PAT, so the automation survives staff turnover.

### App configuration

Create at: **deepgram org → Settings → Developer settings → GitHub Apps → New GitHub App**

| Field | Value |
|---|---|
| GitHub App name | `deepgram-homebrew-bumper` |
| Description | `Bumps deepgram/homebrew-tap Formula/deepgram.rb on each deepgram/cli release.` |
| Homepage URL | `https://github.com/deepgram/cli` |
| Callback URL | *blank* |
| Setup URL | *blank* |
| Webhook → Active | **unchecked** (we don't need event delivery) |

### Permissions (minimum)

**Repository permissions:**

| Permission | Access | Why |
|---|---|---|
| Contents | Read & write | Push the `bump-deepctl-X.Y.Z` branch to homebrew-tap |
| Pull requests | Read & write | Open the bump PR |
| Metadata | Read | Required by GitHub for any app |

**Organization permissions:** none.
**Account permissions:** none.
**Subscribe to events:** none.

### Installation

Install the App on **`deepgram/homebrew-tap` only**. The App does **not** need to be installed on `deepgram/cli` — the workflow in `deepgram/cli` authenticates as the App using its credentials, then mints an installation token scoped to `homebrew-tap`.

### Credentials in `deepgram/cli`

After the App is created, add:

| Type | Location | Name | Value |
|---|---|---|---|
| **Variable** | Settings → Secrets and variables → Actions → Variables | `HOMEBREW_BUMP_APP_ID` | The App's numeric ID |
| **Secret** | Settings → Secrets and variables → Actions → Secrets | `HOMEBREW_BUMP_APP_KEY` | Full contents of the `.pem` private key (including BEGIN/END lines) |

The workflow references these exact names. Until they're populated the `bump-brew-formula` job will fail at the `Mint installation token` step with a clear error message; the rest of the release pipeline (build / test / publish to PyPI / mark-latest / deploy-web) is unaffected.

### Rotation

To rotate the private key:

1. App settings → Generate a new private key
2. Update `HOMEBREW_BUMP_APP_KEY` secret in `deepgram/cli`
3. Revoke the old key in App settings

No downtime, no workflow changes.

## Operational notes

### What if a release ships before the App exists?

The `bump-brew-formula` job will fail. Other release jobs (PyPI publish, mark-latest, deploy-web) succeed independently. To recover: set up the App, then either re-run the failed job from the Actions tab, or run the script manually as described above.

### What if PyPI is slow?

The job waits up to 10 minutes (30 × 20s) for the new version to appear on PyPI's index. If it times out, fail with a clear error and let the operator re-run.

### What if the bump PR conflicts with manual edits in the tap?

The formula is auto-generated and tagged with a banner. Manual edits should be rare. If someone has manually edited the tap (e.g. emergency hotfix), the bump PR will overwrite their changes when merged. To prevent this, edits should always go through the template here, then trigger a fresh bump. The PR body explicitly tells reviewers this.

### What if `homebrew-pypi-poet` produces broken resources?

The bump script depends on a third-party tool. If poet's output format changes (it has been stable for years, but possible), `extract_resource_blocks` will fail with a clear error. Fix is to update the regex in [`scripts/bump_brew_formula.py`](../scripts/bump_brew_formula.py) and re-run.

### Why pin `setuptools<80`?

`homebrew-pypi-poet` imports `pkg_resources`, which was removed from `setuptools` in version 80+. The pin is in the bump tool's private venv only — it does not affect the formula or its dependencies.

## Verifying changes to the template

After editing `scripts/templates/deepgram.rb.template`, run a dry-run against the latest published version:

```bash
python scripts/bump_brew_formula.py --version "$(curl -s https://pypi.org/pypi/deepctl/json | python -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])')" --dry-run > /tmp/deepgram.rb
brew style /tmp/deepgram.rb
```

If the existing formula in `homebrew-tap` is at the same version, you can also diff:

```bash
git clone https://github.com/deepgram/homebrew-tap.git
diff /tmp/deepgram.rb homebrew-tap/Formula/deepgram.rb
```

A clean diff means your template change is purely additive/structural and won't churn the next bump PR.
