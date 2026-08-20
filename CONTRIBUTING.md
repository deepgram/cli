# Contributing

Contributions are welcome! Whether it's a bug fix, new command, documentation improvement, or plugin — we appreciate your help.

## Quick Start

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/cli.git
cd cli

# Install dependencies
uv sync --group dev

# Run all checks
make dev          # format + lint + test
make check        # format + lint + typecheck (no tests)
```

## Development Workflow

1. **Create a branch** from `main`
2. **Make your changes** following the conventions below
3. **Run `make dev`** to ensure everything passes
4. **Run `make readmes`** if you added or modified packages
5. **Submit a pull request**

## Conventions

### Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(speak): add --speed flag for playback rate
fix(keys): handle expired key gracefully
docs(readme): update installation section
test(models): add filter edge case tests
chore: update dependencies
```

### Code Style

- **Formatter/linter:** `ruff` (line-length 88, target py310)
- **Type checker:** `mypy` (strict mode)
- **Imports:** Use `from __future__ import annotations` for forward references
- **Type-only imports:** Move into `TYPE_CHECKING` blocks

### Adding a New Command

The fastest way is to use the scaffolding skill:

```bash
dg new-command
```

Or manually create a package under `packages/` following the existing pattern. Every new package needs updates to:

- `packages/<name>/pyproject.toml` — package metadata and entry point
- Root `pyproject.toml` — dependency + `[tool.uv.sources]`
- `.github/release-please-config.json` — component definition
- `.github/.release-please-manifest.json` — initial version
- `.github/workflows/test.yml` — test path

Then run `make readmes` to update all READMEs.

The root `pyproject.toml` dependency is not optional bookkeeping — it is the
delivery manifest. `pip install --upgrade deepctl` (what `dg update` runs)
only installs what root depends on, so a published package missing from that
list never reaches anyone. `make floors-check` fails on both that omission and
a floor left below the workspace version; run `make floors-fix` to pin floors.

### Releasing

Releases are driven by release-please. Two things are worth knowing before you
run one:

**Land your release-notes edits as a commit, not a PR description edit.**
`sync-release-pr` regenerates `uv.lock` and the root dependency floors on the
release branch automatically, but it pushes with `GITHUB_TOKEN`, which by
design retriggers nothing — so the PR's checks still reflect the bot's first
commit and stay red on content that is now correct. Editing `CHANGELOG.md` on
the branch is a commit and retriggers them. Editing the PR *description* does
not: that fires `pull_request: edited`, which is outside the default trigger
types. If a release ever needs to go out unattended, switch that push to a
dedicated PAT or GitHub App token.

**Nothing advertises a release until it is installable.** `verify-published`
polls PyPI until `pip install deepctl==X` resolves its full closure, then
installs it for real and runs the CLI. `mark-latest`, `deploy-web`, and the
Homebrew bump all wait on it. If it times out, PyPI has not propagated or a
sub-package failed to publish — re-run that one job once PyPI has caught up
and the three downstream jobs re-evaluate.

### Testing

- Unit tests: `packages/*/tests/unit/`
- Integration tests: `tests/`
- Run all: `uv run pytest`
- Run one package: `uv run pytest packages/deepctl-cmd-speak/tests/ -v`
- CI matrix: Python 3.10-3.14, Linux/Windows/macOS

### Plugin Development

Create external plugins that extend the CLI:

```python
from deepctl_core.base_command import BaseCommand

class MyCommand(BaseCommand):
    name = "mycommand"
    help = "My custom command"

    def handle(self, config, auth_manager, client, **kwargs):
        pass
```

Register in your plugin's `pyproject.toml`:

```toml
[project.entry-points."deepctl.plugins"]
mycommand = "my_plugin.command:MyCommand"
```

See [`packages/deepctl-plugin-example`](packages/deepctl-plugin-example) for a complete example.

## Reporting Issues

- **Bugs:** Use the [bug report template](https://github.com/deepgram/cli/issues/new?template=bug_report.yml)
- **Features:** Use the [feature request template](https://github.com/deepgram/cli/issues/new?template=feature_request.yml)
- **Docs:** Use the [docs improvement template](https://github.com/deepgram/cli/issues/new?template=docs_improvement.yml)
- **Security:** See [SECURITY.md](.github/SECURITY.md)

## Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
