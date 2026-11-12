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

- `packages/<name>/pyproject.toml` — package metadata and entry point, at the root version
- Root `pyproject.toml` — dependency + `[tool.uv.sources]`
- `.github/release-please-config.json` — the package's `pyproject.toml` in the root component's `extra-files`
- `.github/workflows/test.yml` — test path

Then run `make floors-fix` (pins and back-edge) and `make readmes`.

Every first-party package ships at one version (lockstep). The root
`pyproject.toml` pins each package `==` that version and is the delivery
manifest: `pip install --upgrade deepctl` (what `dg update` runs) only installs
what root depends on. Every package pins its first-party dependencies `==` the
same version and declares `deepctl==` it too (the back-edge), so upgrading any
one package moves the whole set or the resolver refuses. Each of those pin
lines carries `# x-release-please-version` so release-please moves it.
`make floors-check` fails on any version or pin that differs, a missing
back-edge, or a package root does not list; `make floors-fix` repairs them.
`deepctl-plugin-example` is the exception: it is the third-party fixture and
versions on its own. The per-package `packages/*/CHANGELOG.md` files stop at
each package's last separate release; the root `CHANGELOG.md` covers every
package from the first lockstep release on.

### Releasing

Releases are driven by release-please. Two things are worth knowing before you
run one:

**One release PR, one version, one tag.** release-please bumps every
package's version and every marked pin. The release workflow then re-pins the
release branch in two jobs: `sync-release-pr` runs `make floors-fix` and
`uv lock` with no credentials and hands over a patch, and `push-release-pr`
applies it and pushes with the release GitHub App's token, so the required
checks run on the release PR's final head (a `GITHUB_TOKEN` push would
retrigger nothing). A release PR is only green once that push has landed. If
the App isn't set up (the `RELEASE_APP_CLIENT_ID` variable and the
`RELEASE_APP_PRIVATE_KEY` secret of the `release` environment, which only
`main` may deploy to), `push-release-pr` fails at minting the token rather
than leaving the checks stale. Land release-notes edits as a commit on the
branch, not as a PR description edit: a description edit fires
`pull_request: edited`, which is outside the default trigger types.

**Nothing advertises a release until it is installable.** `verify-published`
polls PyPI until `pip install deepctl==X` resolves its full closure, then
installs it for real and runs the CLI. `mark-latest`, `deploy-web`, the
Homebrew bump, and `publish-npm` all wait on it. `deploy-web` also waits on
`publish-npm`, which publishes `@deepgram/cli` and smoke-tests it with `npx`,
so the site never shows npm install instructions before the package works.
If `verify-published` times out, PyPI has not propagated or a sub-package
failed to publish — re-run all jobs on that workflow run once PyPI has caught
up; re-running only the failed job leaves the downstream jobs skipped. If
only `publish-npm` fails, fix the cause and re-run failed jobs: `deploy-web`
stays skipped until it passes, and the npm publish step skips an
already-published version. Re-running all jobs is safe: the publish step uses
`skip-existing`.

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

Don't reuse a built-in command name. dg ignores a plugin command named like a
built-in (for example `login`, or `audio` under `debug`) and prints a warning.
Run `dg --help` for the current names, and give your command a name specific
to your plugin so a future built-in doesn't take it.

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
